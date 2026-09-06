"""Library-conditioned features over the user's positive-engagement subset P.

The classifier previously had no idea what the user actually reads — it only
saw `corpus_affinity` to the declared `research_goals` text. This module
closes that gap with features computed against the **positive-engagement
subset** P, defined as Zotero items the user has actively engaged with
(tags / annotations / notes), excluding UI-batch dismissals and items
merely sitting in a collection.

Features (5 dims total):
  Sprint 1
    - ``nearest_kept_cosine``        max cosine to any P row
    - ``positive_centroid_cosine``   cosine to mean(P)
  Sprint 2
    - ``recent_centroid_cosine``     cosine to mean(P ∩ last 90 days)
    - ``topic_drift``                recent_centroid − positive_centroid

The fifth feature counts overlapping author surnames. Candidate paper groups
are excluded from all five features, including the author set.

P is materialised from the same golden CSV the classifier trains on, which
keeps the features in sync with the labels by construction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from zotero_summarizer.domain import paper_group_id
from zotero_summarizer.storage.corpus_types import CorpusAffinity

LOGGER = logging.getLogger(__name__)


# Positive engagement requires at least one of these tier markers in the
# `gold_signal_tier` audit column (see goldenset._format_tier_audit). The
# four "meta / first_glance / hard_veto / trash" tiers are explicitly
# excluded — none of them are positive engagement signals.
POSITIVE_TIER_MARKERS = (
    "strong_positive",
    "high_positive",
    "medium_positive",
    "critical_engagement",
    "ann=",
    "notes=",
)

# Sprint-2 "recent" window in days. The user adds Zotero items as they read;
# the 90-day window is a deliberate trade-off between picking up new
# research streams (smaller window) and having enough data for a stable
# centroid (larger window).
RECENT_WINDOW_DAYS = 90


def _is_positive_engagement(row: dict[str, str]) -> bool:
    """True iff the row carries at least one positive-engagement marker.

    Conservative — `first_glance` (UI batch), `meta` (passive collection),
    `hard_veto` and `trash` are not engagement and must NOT enter P.
    """
    if str(row.get("in_trash", "")).strip().lower() in ("true", "1"):
        return False
    tier = (row.get("gold_signal_tier") or "").strip()
    if not tier:
        return False
    if tier in {"meta", "first_glance", "hard_veto", "trash"}:
        return False
    return any(marker in tier for marker in POSITIVE_TIER_MARKERS)


def _parse_days_since(row: dict[str, str]) -> int:
    """Read the `days_since_added` column safely.

    Goldenset writes ``-1`` for rows whose date couldn't be parsed (and the
    `feed:` UI-batch rows have ``-1`` because they come from
    inferred metadata, not from Zotero's dateAdded). Such rows are treated
    as "very old" (∞) so they fall out of the recent window.
    """
    raw = (row.get("days_since_added") or "").strip()
    if not raw or raw == "-1":
        return 10**9
    if raw.lstrip("-").isdigit():
        v = int(raw)
        return v if v >= 0 else 10**9
    return 10**9


@dataclass(frozen=True)
class PositiveLibrary:
    """Compiled positive-engagement embedding set.

    ``embeddings`` is (n, EMBEDDING_DIM) float32, already L2-normalised so
    cosine reduces to a dot product. ``centroid`` and ``recent_centroid``
    are the L2-normalised mean embeddings of the full set and the
    recent-window subset (or zero vectors if either is empty).
    """

    embeddings: np.ndarray
    centroid: np.ndarray
    recent_centroid: np.ndarray
    paper_groups: tuple[str, ...]
    authors_lower: frozenset[str]
    author_tokens: tuple[frozenset[str], ...]
    # Raw vectors and recency survive persistence so excluding a whole paper
    # group can rebuild both centroids without its self-match.
    raw_embeddings: np.ndarray
    recent_mask: np.ndarray

    @property
    def n_rows(self) -> int:
        return self.embeddings.shape[0]


def _l2_normalise(vectors: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation. Zero rows stay zero (cosine = 0)."""
    if vectors.ndim == 1:
        norm = np.linalg.norm(vectors)
        if norm == 0:
            return vectors
        return vectors / norm
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


def _author_tokens(authors_str: str) -> set[str]:
    """Split an authors string on common separators and normalise.

    Authors come from Zotero/RSS as "Last, First; Last, First" or
    "First Last, First Last". The token set we keep is the lower-cased
    surname (everything before the first comma, or the last whitespace-
    separated word) for each author. Cheap, collision-prone (Wang/Li),
    but a strict superset of what an OpenAlex-author-ID matcher would
    return — F1-positive, F1-negative is unchanged. Future Sprint-3 work
    can swap this for OpenAlex IDs without changing the feature contract.
    """
    if not authors_str:
        return set()
    tokens: set[str] = set()
    for chunk in authors_str.replace("&", ";").replace(" and ", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "," in chunk:
            surname = chunk.split(",", 1)[0].strip()
        else:
            parts = chunk.split()
            surname = parts[-1] if parts else ""
        surname = surname.strip().lower()
        if surname and len(surname) >= 2:
            tokens.add(surname)
    return tokens


def load_positive_library_from_rows(
    rows: list[dict[str, str]],
    corpus_db_path: Path,
) -> PositiveLibrary:
    """Build P from already-loaded golden rows."""
    from zotero_summarizer.services.model import classifier

    valid = [
        row for row in rows
        if _is_positive_engagement(row)
        and (row.get("title") or "").strip()
        and (row.get("abstract") or "").strip()
        and (row.get("item_key") or "").strip()
    ]
    keys = [(row.get("item_key") or "").strip() for row in valid]
    # Batched (GPU) encode of the whole P-set instead of one call per row.
    embeddings = classifier.get_or_compute_embeddings_batch(
        corpus_db_path,
        [{"item_key": keys[i],
          "title": (row.get("title") or "").strip(),
          "abstract": (row.get("abstract") or "").strip()}
         for i, row in enumerate(valid)],
    )
    raw_embeddings = [embeddings[i] for i in range(len(valid))]
    recent_mask = [_parse_days_since(row) <= RECENT_WINDOW_DAYS for row in valid]
    return _stack_library(valid, raw_embeddings, recent_mask)


def positive_library_from_embeddings(
    rows: list[dict[str, str]],
    embeddings: np.ndarray,
) -> PositiveLibrary:
    """Build P from rows whose embeddings are ALREADY computed.

    ``rows[i]`` aligns with ``embeddings[i]``. Used by
    per-fold cross-validation to rebuild P from a train fold without re-reading
    the embedding cache — which also keeps it unit-testable without SPECTER2.
    """
    positive_rows: list[dict[str, str]] = []
    raw: list[np.ndarray] = []
    recent_mask: list[bool] = []
    for i, row in enumerate(rows):
        if not _is_positive_engagement(row):
            continue
        positive_rows.append(row)
        raw.append(np.asarray(embeddings[i], dtype=np.float32))
        recent_mask.append(_parse_days_since(row) <= RECENT_WINDOW_DAYS)
    return _stack_library(positive_rows, raw, recent_mask)


def _empty_library() -> PositiveLibrary:
    from zotero_summarizer.services.model import classifier

    zeros = np.zeros((0, classifier.EMBEDDING_DIM), dtype=np.float32)
    centroid_zero = np.zeros(classifier.EMBEDDING_DIM, dtype=np.float32)
    return PositiveLibrary(
        embeddings=zeros,
        centroid=centroid_zero,
        recent_centroid=centroid_zero,
        paper_groups=tuple(),
        authors_lower=frozenset(),
        author_tokens=tuple(),
        raw_embeddings=zeros,
        recent_mask=np.zeros((0,), dtype=bool),
    )


def recompute_engagement_columns(
    X: np.ndarray, rows: list[dict[str, str]], train_idx: np.ndarray,
    corpus: CorpusAffinity | None,
) -> np.ndarray:
    """Copy X with corpus affinity and five library columns from train rows.

    Rows align with X. Reuse their embeddings; do not reread the corpus or
    retain held-out engagement in the positive library.
    """
    from zotero_summarizer.services.model import classifier

    emb = classifier.EMBEDDING_DIM
    library = positive_library_from_embeddings(
        [rows[i] for i in train_idx], X[train_idx, :emb],
    )
    result = X.copy()
    result[:, emb + 5] = corpus.scores(train_idx) if corpus is not None else 0
    lo = emb + 7
    for i, row in enumerate(rows):
        result[i, lo:lo + 5] = compute_library_features(
            X[i, :emb], library, candidate_row=row,
        )
    return result


def _stack_library(
    rows: list[dict[str, str]],
    raw_embeddings: list[np.ndarray],
    recent_mask: list[bool],
) -> PositiveLibrary:
    """Internal — turn the raw collected embeddings into a `PositiveLibrary`."""
    if not raw_embeddings:
        LOGGER.warning(
            "positive-engagement subset P is EMPTY — library features will "
            "evaluate to zero. Check `gold_signal_tier` distribution in the "
            "training rows."
        )
        return _empty_library()

    stacked = np.vstack(raw_embeddings).astype(np.float32)
    normalised = _l2_normalise(stacked).astype(np.float32)
    centroid = _l2_normalise(stacked.mean(axis=0)).astype(np.float32)
    if any(recent_mask):
        recent_stack = stacked[np.asarray(recent_mask, dtype=bool)]
        recent_centroid = _l2_normalise(recent_stack.mean(axis=0)).astype(np.float32)
    else:
        # No recent items — fall back to the global centroid (topic_drift = 0).
        recent_centroid = centroid
    authors = tuple(frozenset(_author_tokens(row.get("authors") or "")) for row in rows)
    authors_lower = frozenset().union(*authors)
    LOGGER.info(
        "loaded positive-engagement subset: n=%d, n_recent=%d, n_authors=%d",
        normalised.shape[0], int(sum(recent_mask)), len(authors_lower),
    )
    return PositiveLibrary(
        embeddings=normalised,
        centroid=centroid,
        recent_centroid=recent_centroid,
        paper_groups=tuple(paper_group_id(row) for row in rows),
        authors_lower=authors_lower,
        author_tokens=authors,
        raw_embeddings=stacked,
        recent_mask=np.asarray(recent_mask, dtype=bool),
    )


def _exclusion_mask(library: PositiveLibrary, exclude_group: str | None) -> np.ndarray | None:
    """Mask the candidate's entire paper group; legacy payloads have no groups."""
    if not exclude_group or not library.paper_groups:
        return None
    mask = np.fromiter(
        (group == exclude_group for group in library.paper_groups),
        dtype=bool, count=len(library.paper_groups),
    )
    return mask if mask.any() else None


def _cosines_over_kept(
    cand: np.ndarray, library: PositiveLibrary, keep: np.ndarray,
) -> tuple[float, float, float]:
    """nearest / centroid / recent cosines computed over the kept P rows only
    (the leave-one-out path). Centroids are rebuilt from the raw embeddings."""
    if not keep.any():
        return 0.0, 0.0, 0.0
    nearest = float(np.max((library.embeddings @ cand)[keep]))
    centroid = _l2_normalise(library.raw_embeddings[keep].mean(axis=0))
    recent_keep = library.recent_mask & keep
    recent = (
        _l2_normalise(library.raw_embeddings[recent_keep].mean(axis=0))
        if recent_keep.any() else centroid
    )
    return nearest, float(centroid @ cand), float(recent @ cand)


def _author_overlap(
    library: PositiveLibrary, candidate_authors: str, excluded: np.ndarray | None,
) -> float:
    authors = library.authors_lower if excluded is None else frozenset().union(*(
        tokens for tokens, drop in zip(library.author_tokens, excluded, strict=True) if not drop
    ))
    if authors and candidate_authors:
        overlap = len(_author_tokens(candidate_authors) & authors)
        return float(min(overlap, 5))
    return 0.0


def compute_library_features(
    candidate_embedding: np.ndarray,
    library: PositiveLibrary,
    *,
    candidate_row: dict[str, str] | None = None,
) -> tuple[float, float, float, float, float]:
    """Return library features for one candidate.

    Order matches the layout in :func:`classifier._extra_features`:
      0  nearest_kept_cosine
      1  positive_centroid_cosine
      2  recent_centroid_cosine
      3  topic_drift  (recent − all-time, captures interest drift)
      4  author_overlap_count (clipped to [0, 5])

    ``candidate_row`` supplies authors and paper identity. The whole candidate
    group is excluded from embeddings, centroids AND authors. A genuinely new
    paper has no match in P. All five are zero when the library is empty.
    """
    if library.n_rows == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    cand = _l2_normalise(candidate_embedding.astype(np.float32))
    excl = _exclusion_mask(library, paper_group_id(candidate_row) if candidate_row is not None else None)
    if excl is None:
        nearest = float(np.max(library.embeddings @ cand))
        centroid_cos = float(library.centroid @ cand)
        recent_cos = float(library.recent_centroid @ cand)
    else:
        nearest, centroid_cos, recent_cos = _cosines_over_kept(cand, library, ~excl)
    drift = recent_cos - centroid_cos
    author_overlap = _author_overlap(
        library, (candidate_row.get("authors") or "") if candidate_row is not None else "", excl,
    )
    return nearest, centroid_cos, recent_cos, drift, author_overlap
