"""BM25 (Okapi) lexical index over the corpus text — the lexical leg of Library
hybrid search.

In-memory `rank_bm25` index over each corpus item's title + abstract + tags,
rebuilt when the SQLite main/WAL fingerprint changes,
so repeated searches reuse it. Pure-Python, no DB migration. A process-level
singleton (``get_corpus_bm25``) keeps the index resident across requests.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

from zotero_summarizer.storage.corpus import open_corpus_conn
from zotero_summarizer.storage.corpus_read import _corpus_fingerprint

from rank_bm25 import BM25Okapi

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens — the single tokenizer shared with the
    faithbench corpus (``services/faithbench/_corpus.py`` re-exports it)."""
    return _TOKEN_RE.findall((text or "").lower())


def _parse_tags(raw: str | None) -> list[str]:
    value = json.loads(raw if raw is not None else "[]")
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise ValueError("Corpus tags must be a JSON list of strings")
    return value


class CorpusBM25:
    """In-memory BM25 index over the corpus text. Rebuilt only on corpus change."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._keys: list[str] = []
        self._bm25 = None  # BM25Okapi | None

    def _conn(self) -> sqlite3.Connection:
        return open_corpus_conn(self.db_path)

    def _ensure_index(self) -> None:
        """Build/refresh the index if the corpus changed. Caller holds ``_lock``."""
        # ponytail: DB-wide invalidation; table revisions if unrelated cache churn dominates.
        version = _corpus_fingerprint(str(self.db_path))
        if version == self._version:
            return
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT item_id, title, abstract, tags_json FROM corpus_embeddings"
            ).fetchall()
        finally:
            conn.close()
        keys: list[str] = []
        docs: list[list[str]] = []
        for r in rows:
            text = " ".join((
                str(r["title"] or ""),
                str(r["abstract"] or ""),
                " ".join(_parse_tags(r["tags_json"])),
            ))
            keys.append(str(r["item_id"]))
            docs.append(tokenize(text))
        bm25 = BM25Okapi(docs) if any(docs) else None
        self._keys, self._bm25, self._version = keys, bm25, version

    def search(self, query: str, candidate_keys: list[str], top_k: int = 100) -> dict[str, float]:
        """``{item_key: bm25 score}`` for ``candidate_keys``, top_k by score.
        Empty when the corpus/query has no tokens or no candidate scores positive.
        Dependency, storage and indexing errors propagate."""
        q_tokens = tokenize(query)
        if not q_tokens or not candidate_keys:
            return {}
        with self._lock:
            self._ensure_index()
            if self._bm25 is None:
                return {}
            scores = self._bm25.get_scores(q_tokens)
            keys = self._keys
        candidate = set(candidate_keys)
        pairs = [
            (keys[i], float(scores[i]))
            for i in range(len(keys))
            if keys[i] in candidate and scores[i] > 0
        ]
        pairs.sort(key=lambda kv: kv[1], reverse=True)
        return dict(pairs[:top_k])

    def texts_for(self, keys: list[str]) -> dict[str, str]:
        """``{item_key: "title. abstract"}`` for the rerank input. One IN-query."""
        ids = [str(k) for k in keys if str(k or "").strip()]
        if not ids:
            return {}
        conn = self._conn()
        try:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT item_id, title, abstract FROM corpus_embeddings WHERE item_id IN ({placeholders})",
                ids,
            ).fetchall()
        finally:
            conn.close()
        return {
            str(r["item_id"]): f"{str(r['title'] or '').strip()}. {str(r['abstract'] or '').strip()}".strip()
            for r in rows
        }


_INSTANCES: dict[str, CorpusBM25] = {}
_INSTANCES_LOCK = threading.Lock()


def get_corpus_bm25(db_path: Path) -> CorpusBM25:
    """Process-level singleton per corpus DB, so the BM25 index persists across
    searches (rebuilt only when the corpus changes)."""
    key = str(db_path)
    with _INSTANCES_LOCK:
        inst = _INSTANCES.get(key)
        if inst is None:
            inst = CorpusBM25(db_path)
            _INSTANCES[key] = inst
        return inst
