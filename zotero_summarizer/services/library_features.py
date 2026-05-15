"""Library-conditioned features over the user's positive-engagement subset P.

The classifier previously had no idea what the user actually reads — it only
saw `corpus_affinity` to the declared `research_goals` text. This module
closes that gap with features computed against the **positive-engagement
subset** P, defined as Zotero items the user has actively engaged with
(tags / annotations / notes), excluding UI-batch dismissals and items
merely sitting in a collection.

Features (4 dims total):
  Sprint 1
    - ``nearest_kept_cosine``        max cosine to any P row
    - ``positive_centroid_cosine``   cosine to mean(P)
  Sprint 2
    - ``recent_centroid_cosine``     cosine to mean(P ∩ last 90 days)
    - ``topic_drift``                recent_centroid − positive_centroid

Author/venue overlap (Sprint 2 extension) is exposed separately by the
caller because it needs the raw author string at predict time. See
:func:`author_overlap` below.

P is materialised from the same golden CSV the classifier trains on, which
keeps the features in sync with the labels by construction.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np


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
    item_keys: tuple[str, ...]
    authors_lower: frozenset[str]

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


def _collect_author_tokens(rows: list[dict[str, str]]) -> frozenset[str]:
    out: set[str] = set()
    for row in rows:
        if not _is_positive_engagement(row):
            continue
        out.update(_author_tokens((row.get("authors") or "")))
    return frozenset(out)


def load_positive_library_from_rows(
    rows: list[dict[str, str]],
    corpus_db_path: Path,
) -> PositiveLibrary:
    """Build P from already-loaded golden rows."""
    from zotero_summarizer.services import classifier

    keys: list[str] = []
    raw_embeddings: list[np.ndarray] = []
    recent_mask: list[bool] = []
    for row in rows:
        if not _is_positive_engagement(row):
            continue
        title = (row.get("title") or "").strip()
        abstract = (row.get("abstract") or "").strip()
        item_key = (row.get("item_key") or "").strip()
        if not title or not abstract or not item_key:
            continue
        emb = classifier.get_or_compute_embedding(
            corpus_db_path, item_key, title, abstract,
        )
        keys.append(item_key)
        raw_embeddings.append(emb)
        recent_mask.append(_parse_days_since(row) <= RECENT_WINDOW_DAYS)
    authors = _collect_author_tokens(rows)
    return _stack_library(keys, raw_embeddings, recent_mask, authors)


def load_positive_library(
    golden_csv: Path,
    corpus_db_path: Path,
) -> PositiveLibrary:
    """Build the positive-engagement subset P from the golden CSV."""
    if not golden_csv.exists():
        raise FileNotFoundError(f"golden CSV missing for P-set: {golden_csv}")
    with golden_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return load_positive_library_from_rows(rows, corpus_db_path)


def _empty_library() -> PositiveLibrary:
    from zotero_summarizer.services import classifier

    zeros = np.zeros((0, classifier.EMBEDDING_DIM), dtype=np.float32)
    centroid_zero = np.zeros(classifier.EMBEDDING_DIM, dtype=np.float32)
    return PositiveLibrary(
        embeddings=zeros,
        centroid=centroid_zero,
        recent_centroid=centroid_zero,
        item_keys=tuple(),
        authors_lower=frozenset(),
    )


def _stack_library(
    keys: list[str],
    raw_embeddings: list[np.ndarray],
    recent_mask: list[bool],
    authors_lower: frozenset[str],
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
    LOGGER.info(
        "loaded positive-engagement subset: n=%d, n_recent=%d, n_authors=%d",
        normalised.shape[0], int(sum(recent_mask)), len(authors_lower),
    )
    return PositiveLibrary(
        embeddings=normalised,
        centroid=centroid,
        recent_centroid=recent_centroid,
        item_keys=tuple(keys),
        authors_lower=authors_lower,
    )


def compute_library_features(
    candidate_embedding: np.ndarray,
    library: PositiveLibrary,
    *,
    candidate_authors: str = "",
) -> tuple[float, float, float, float, float]:
    """Return library features for one candidate.

    Order matches the layout in :func:`classifier._extra_features`:
      0  nearest_kept_cosine
      1  positive_centroid_cosine
      2  recent_centroid_cosine
      3  topic_drift  (recent − all-time, captures interest drift)
      4  author_overlap_count (clipped to [0, 5])

    All five default to 0.0 when the library is empty.
    """
    if library.n_rows == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    cand = _l2_normalise(candidate_embedding.astype(np.float32))
    nearest = float(np.max(library.embeddings @ cand))
    centroid_cos = float(library.centroid @ cand)
    recent_cos = float(library.recent_centroid @ cand)
    drift = recent_cos - centroid_cos
    if library.authors_lower and candidate_authors:
        overlap = len(_author_tokens(candidate_authors) & library.authors_lower)
        author_overlap = float(min(overlap, 5))
    else:
        author_overlap = 0.0
    return nearest, centroid_cos, recent_cos, drift, author_overlap
