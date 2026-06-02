"""BM25 (Okapi) lexical index over the corpus text — the lexical leg of Library
hybrid search.

In-memory `rank_bm25` index over each corpus item's title + abstract + tags,
rebuilt only when the corpus changes (keyed by row count + ``MAX(updated_at)``),
so repeated searches reuse it. Pure-Python, no DB migration. A process-level
singleton (``get_corpus_bm25``) keeps the index resident across requests.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path

try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover - optional dependency boundary
    BM25Okapi = None

LOGGER = logging.getLogger("zotero_summarizer.corpus_bm25")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _parse_tags(raw: str | None) -> list[str]:
    # Boundary parse of a DB JSON column (mirrors EmbeddingCache._parse_list):
    # corrupt tags must not break search, so fall back to no tags.
    try:
        value = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


class CorpusBM25:
    """In-memory BM25 index over the corpus text. Rebuilt only on corpus change."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._version: tuple[int, str] | None = None
        self._keys: list[str] = []
        self._bm25 = None  # BM25Okapi | None

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _db_version(conn: sqlite3.Connection) -> tuple[int, str]:
        row = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS m FROM corpus_embeddings"
        ).fetchone()
        return (int(row["c"] or 0), str(row["m"] or ""))

    def _ensure_index(self) -> None:
        """Build/refresh the index if the corpus changed. Caller holds ``_lock``."""
        conn = self._conn()
        try:
            version = self._db_version(conn)
            if self._bm25 is not None and version == self._version:
                return
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
            docs.append(_tokenize(text))
        self._keys = keys
        self._version = version
        self._bm25 = BM25Okapi(docs) if (BM25Okapi is not None and docs) else None
        if BM25Okapi is None:
            LOGGER.warning("rank_bm25 unavailable; BM25 leg of hybrid search is off (dense-only)")

    def search(self, query: str, candidate_keys: list[str], top_k: int = 100) -> dict[str, float]:
        """``{item_key: bm25 score}`` for ``candidate_keys``, top_k by score.
        Empty when rank_bm25 is unavailable, the corpus is empty, the query has no
        tokens, or no candidate scores positive."""
        q_tokens = _tokenize(query)
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
