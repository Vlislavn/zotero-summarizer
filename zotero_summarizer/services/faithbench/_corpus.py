"""Paper text for the benchmark: selection, extraction, freezing, normalization,
chunking and a tiny per-paper BM25 chunk index.

**Frozen text is the ground-truth substrate.** ``build`` extracts each paper's
PDF once and writes the raw text to ``papers/<item_key>.txt`` with a sha256
recorded in the benchmark file. ``run`` and ``judge`` always read the frozen
file and verify the sha — PDF re-extraction drift therefore becomes a
``HARNESS_FAULT``, never a model failure.

``normalize_text`` is the single normalization used by *both* the build-time
span gate and the judge's hard checkers, so "verbatim" means the same thing in
both places.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.services.faithbench._constants import (
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    MIN_PAPER_CHARS,
)
from zotero_summarizer.storage.corpus_bm25 import tokenize  # the single word tokenizer (used by PaperChunkIndex)

LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")  # normalize_text only; word tokens live in storage.corpus_bm25.tokenize
_ARTICLES = frozenset({"a", "an", "the"})
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# Normalization (SQuAD-style) — shared by build gate and judge hard checkers
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """NFKC → casefold → drop punctuation/articles → collapse whitespace."""
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    tokens = [t for t in _TOKEN_RE.findall(folded) if t not in _ARTICLES]
    return " ".join(tokens)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def sentence_at(text: str, offset: int) -> str:
    """The sentence containing character ``offset`` (evidence for the judge)."""
    pos = 0
    for sentence in split_sentences(text):
        start = text.find(sentence, pos)
        if start < 0:
            continue
        end = start + len(sentence)
        if start <= offset < end:
            return sentence.strip()
        pos = end
    return ""


# ---------------------------------------------------------------------------
# Frozen-text persistence
# ---------------------------------------------------------------------------


def paper_text_path(papers_dir: Path, item_key: str) -> Path:
    return papers_dir / f"{item_key}.txt"


def freeze_paper_text(papers_dir: Path, item_key: str, text: str) -> str:
    """Persist the raw extracted text; returns its sha256."""
    papers_dir.mkdir(parents=True, exist_ok=True)
    paper_text_path(papers_dir, item_key).write_text(text, encoding="utf-8")
    return sha256_text(text)


def load_frozen_text(papers_dir: Path, item_key: str, *, expected_sha256: str) -> str:
    """Read a frozen paper text and verify integrity.

    Raises ``FileNotFoundError`` / ``ValueError`` — the caller (judge/runner)
    converts these into ``HARNESS_FAULT`` for the affected items; they must
    never be silently absorbed here.
    """
    path = paper_text_path(papers_dir, item_key)
    text = path.read_text(encoding="utf-8")
    actual = sha256_text(text)
    if actual != expected_sha256:
        raise ValueError(
            f"frozen text drift for {item_key}: sha {actual[:12]} != recorded {expected_sha256[:12]}"
        )
    return text


# ---------------------------------------------------------------------------
# Paper selection + extraction (build stage)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaperRecord:
    item_key: str
    title: str
    text: str
    text_sha256: str


def _pdf_under_root(pdf_path: str, pdf_root: Path) -> bool:
    candidate = Path(pdf_path).expanduser().resolve()
    allowed = pdf_root.expanduser().resolve()
    return allowed in [candidate, *candidate.parents]


def select_papers(
    *,
    reader: Any,
    extractor: Any,
    papers_dir: Path,
    pdf_root: Path,
    n_papers: int,
    item_keys: list[str] | None = None,
    collection: str | None = None,
    tag: str | None = None,
    min_chars: int = MIN_PAPER_CHARS,
    progress_cb: Callable[[str], None] | None = None,
) -> list[PaperRecord]:
    """Pick the first ``n_papers`` candidates with a usable local PDF, extract
    and freeze their text.

    Candidates without a PDF, outside ``PDF_ROOT``, or with < ``min_chars`` of
    extracted text are *skipped with a logged reason* — that is the documented
    selection contract of the build stage (the benchmark needs full texts),
    not error masking; a corrupt PDF still raises out of the extractor.
    """
    if item_keys:
        candidates = [{"item_key": k} for k in item_keys]
    else:
        listing = reader.get_all_items(collection_key=collection, tag=tag)
        candidates = list(listing.get("items") or [])

    records: list[PaperRecord] = []
    for row in candidates:
        if len(records) >= max(1, n_papers):
            break
        item_key = str(row.get("item_key") or "")
        detail = reader.get_item_detail(item_key)
        if detail is None:
            LOGGER.info("faithbench build: %s vanished from Zotero, skipping", item_key)
            continue
        title = str(detail.get("title") or "") or "Untitled"
        pdf_path = str(detail.get("pdf_path") or "")
        if not pdf_path:
            LOGGER.info("faithbench build: %s has no local PDF, skipping", item_key)
            continue
        if not _pdf_under_root(pdf_path, pdf_root):
            LOGGER.warning("faithbench build: %s PDF outside PDF_ROOT, skipping", item_key)
            continue
        text = str(extractor.extract_text(pdf_path) or "").strip()
        if len(text) < min_chars:
            LOGGER.info(
                "faithbench build: %s text too short (%d < %d chars), skipping",
                item_key, len(text), min_chars,
            )
            continue
        sha = freeze_paper_text(papers_dir, item_key, text)
        records.append(PaperRecord(item_key=item_key, title=title, text=text, text_sha256=sha))
        if progress_cb:
            progress_cb(f"froze {item_key} ({len(text)} chars): {title[:60]}")

    if not records:
        raise RuntimeError(
            "faithbench build: no usable papers found (need local PDFs with "
            f">= {min_chars} extracted chars under PDF_ROOT)"
        )
    return records


# ---------------------------------------------------------------------------
# Chunking + per-paper BM25 index (retrieval condition / claim judging)
# ---------------------------------------------------------------------------


def chunk_text(
    text: str, *, chunk_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Fixed-size character windows with overlap, snapped to whitespace."""
    if chunk_chars <= 0 or overlap >= chunk_chars:
        raise ValueError("chunk_chars must be positive and exceed overlap")
    body = text or ""
    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = min(len(body), start + chunk_chars)
        if end < len(body):
            space = body.rfind(" ", start + chunk_chars // 2, end)
            if space > start:
                end = space
        chunk = body[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(body):
            break
        start = max(end - overlap, start + 1)
    return chunks


class PaperChunkIndex:
    """In-memory BM25 over one paper's chunks (lexical leg only — the
    deliberate v1 simplification; the future product pipeline is hybrid).

    Falls back to token-overlap scoring when ``rank_bm25`` is unavailable —
    the same optional-dependency boundary as ``storage/corpus_bm25.py``.
    """

    def __init__(self, text: str, *, chunk_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> None:
        self.chunks = chunk_text(text, chunk_chars=chunk_chars, overlap=overlap)
        self._docs = [tokenize(c) for c in self.chunks]
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:  # optional dependency boundary (mirrors corpus_bm25)
            BM25Okapi = None
            LOGGER.warning("rank_bm25 unavailable; chunk retrieval falls back to token overlap")
        self._bm25 = BM25Okapi(self._docs) if (BM25Okapi is not None and self._docs) else None

    def top_chunks(self, query: str, k: int) -> list[str]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.chunks:
            return []
        if self._bm25 is not None:
            scores = list(self._bm25.get_scores(q_tokens))
        else:
            q_set = set(q_tokens)
            scores = [float(len(q_set.intersection(doc))) for doc in self._docs]
        order = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        return [self.chunks[i] for i in order[: max(1, k)] if scores[i] > 0]
