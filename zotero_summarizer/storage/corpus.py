from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Sequence

from zotero_summarizer.domain import (
    FeedbackSignal,
    TRIAGE_APPROVED_TAG,
    TRIAGE_APPROVED_TAG_TOKEN,
    TRIAGE_REJECTED_TAG,
    TRIAGE_REJECTED_TAG_TOKEN,
)
from zotero_summarizer.models import CorpusItem
from zotero_summarizer.storage.corpus_read import CorpusReadMixin
from zotero_summarizer.storage.corpus_types import CorpusMatchResult  # noqa: F401

from sentence_transformers import SentenceTransformer


LOGGER = logging.getLogger("zotero_summarizer.embedding_cache")


def open_corpus_conn(db_path: Path) -> sqlite3.Connection:
    """Open a ``sqlite3.Row``-backed connection to the corpus DB, WAL enabled.

    Shared by :class:`EmbeddingCache` and ``storage.corpus_bm25.CorpusBM25`` —
    both read/write the same corpus DB, and WAL is DB-sticky, so either side
    enabling it benefits both.
    """
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        conn.close()
        raise
    return conn


class EmbeddingCache(CorpusReadMixin):
    """Stores and queries library embeddings for corpus-aware triage.

    Read/query methods (match + metadata) live in ``CorpusReadMixin``.
    """

    def __init__(self, db_path: Path, model_name: str) -> None:
        self.db_path = db_path
        self.model_name = model_name
        self._encoder_id = json.dumps(["sentence-transformers", version("sentence-transformers"), model_name])
        self._model = None
        # SentenceTransformer/torch inference is not safe to call from multiple
        # threads on one shared model; the backlog drain scores survivors on a
        # thread pool, so serialize the embedding forward pass. Only the fast
        # torch step is guarded — the slow LLM HTTP calls still overlap.
        self._embed_lock = threading.Lock()
        # Cached corpus matrix for the vectorized affinity_and_goals() fast path:
        # {fingerprint, stale_days, matrix (np float32 N×dim), weights (np N)}.
        # Rebuilt when the DB fingerprint changes (including external writes) so we
        # parse the (large) embedding set once, not per scored item.
        self._affinity_cache: dict[str, Any] | None = None
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return open_corpus_conn(self.db_path)

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.db_path.touch(mode=0o600)
        else:
            os.chmod(self.db_path, 0o600)
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS corpus_embeddings (
                    item_id             TEXT PRIMARY KEY,
                    title               TEXT NOT NULL,
                    abstract            TEXT,
                    tags_json           TEXT,
                    collections_json    TEXT,
                    annotation_count    INTEGER DEFAULT 0,
                    manual_note_count   INTEGER DEFAULT 0,
                    created_at          TEXT,
                    content_hash        TEXT NOT NULL,
                    embedding_json      TEXT NOT NULL,
                    updated_at          TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goal_embeddings (
                    goal                TEXT PRIMARY KEY,
                    embedding_json      TEXT NOT NULL,
                    updated_at          TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_corpus_updated_at ON corpus_embeddings(updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_corpus_created_at ON corpus_embeddings(created_at)")
            conn.commit()
        finally:
            conn.close()
        from zotero_summarizer.storage.migrations import CORPUS_MIGRATIONS, run_migrations

        run_migrations(self.db_path, "corpus", CORPUS_MIGRATIONS)

    def upsert_goals(self, goals: Sequence[str]) -> None:
        normalized_goals = sorted({str(goal or "").strip() for goal in goals if str(goal or "").strip()})
        conn = self._conn()
        try:
            for goal_text in normalized_goals:
                embedding = self._embed(goal_text)
                conn.execute(
                    """
                    INSERT INTO goal_embeddings (goal, embedding_json, encoder_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(goal) DO UPDATE SET
                        embedding_json = excluded.embedding_json,
                        encoder_id = excluded.encoder_id,
                        updated_at = datetime('now')
                    """,
                    (goal_text, json.dumps(embedding), self._encoder_id),
                )

            if normalized_goals:
                placeholders = ",".join("?" for _ in normalized_goals)
                conn.execute(
                    f"DELETE FROM goal_embeddings WHERE goal NOT IN ({placeholders})",
                    normalized_goals,
                )
            else:
                conn.execute("DELETE FROM goal_embeddings")

            conn.commit()
        finally:
            conn.close()

    def clear_corpus_embeddings(self) -> int:
        conn = self._conn()
        try:
            row = conn.execute("SELECT COUNT(*) AS total FROM corpus_embeddings").fetchone()
            conn.execute("DELETE FROM corpus_embeddings")
            conn.commit()
            return int(row["total"] or 0) if row else 0
        finally:
            conn.close()

    def upsert_items(self, items: Sequence[CorpusItem], *, missing_item_ids: Sequence[str] = ()) -> tuple[int, int]:
        """Upsert current rows and remove confirmed-missing keys in one transaction."""
        imported = 0
        updated = 0
        conn = self._conn()
        try:
            for item in items:
                text = self._build_text(item.title, item.abstract)
                normalized_tags = sorted({str(tag).strip() for tag in item.tags if str(tag).strip()})
                normalized_collections = sorted({str(name).strip() for name in item.collections if str(name).strip()})
                tags_json = json.dumps(normalized_tags, ensure_ascii=False)
                collections_json = json.dumps(normalized_collections, ensure_ascii=False)
                content_hash = self._content_hash(item.title, item.abstract)
                existing = conn.execute(
                    """
                    SELECT title, abstract, doi, tags_json, collections_json, annotation_count,
                           manual_note_count, created_at, content_hash, encoder_id, embedding_json
                    FROM corpus_embeddings
                    WHERE item_id = ?
                    """,
                    (item.item_id,),
                ).fetchone()

                embedding_current = existing is not None and (
                    existing["content_hash"] == content_hash and existing["encoder_id"] == self._encoder_id
                )
                if existing:
                    metadata_unchanged = (
                        str(existing["title"] or "") == item.title
                        and str(existing["abstract"] or "") == item.abstract
                        and existing["doi"] == item.doi
                        and str(existing["tags_json"] or "[]") == tags_json
                        and str(existing["collections_json"] or "[]") == collections_json
                        and int(existing["annotation_count"] or 0) == int(item.annotation_count)
                        and int(existing["manual_note_count"] or 0) == int(item.manual_note_count)
                        and str(existing["created_at"] or "") == str(item.created_at or "")
                    )
                    if embedding_current and metadata_unchanged:
                        continue

                embedding_json = existing["embedding_json"] if embedding_current else json.dumps(self._embed(text))
                conn.execute(
                    """
                    INSERT INTO corpus_embeddings (
                        item_id, title, abstract, doi, tags_json, collections_json,
                        annotation_count, manual_note_count, created_at,
                        content_hash, embedding_json, encoder_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        title = excluded.title,
                        abstract = excluded.abstract,
                        doi = excluded.doi,
                        tags_json = excluded.tags_json,
                        collections_json = excluded.collections_json,
                        annotation_count = excluded.annotation_count,
                        manual_note_count = excluded.manual_note_count,
                        created_at = excluded.created_at,
                        content_hash = excluded.content_hash,
                        embedding_json = excluded.embedding_json,
                        encoder_id = excluded.encoder_id,
                        updated_at = datetime('now')
                    """,
                    (
                        item.item_id,
                        item.title,
                        item.abstract,
                        item.doi,
                        tags_json,
                        collections_json,
                        int(item.annotation_count),
                        int(item.manual_note_count),
                        item.created_at,
                        content_hash,
                        embedding_json,
                        self._encoder_id,
                    ),
                )
                if existing:
                    updated += 1
                else:
                    imported += 1
            conn.executemany("DELETE FROM corpus_embeddings WHERE item_id = ?", ((key,) for key in missing_item_ids))
            conn.commit()
        finally:
            conn.close()
        return imported, updated

    def _load_model(self):
        if self._model is None:
            LOGGER.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def _embed(self, text: str) -> list[float]:
        with self._embed_lock:  # torch encode is not thread-safe (see __init__)
            model = self._load_model()
            values = [float(v) for v in model.encode(text.strip(), normalize_embeddings=True)]
        if not values or not all(math.isfinite(v) for v in values):
            raise ValueError("Corpus encoder returned an empty or non-finite vector")
        return values

    @staticmethod
    def _build_text(title: str, abstract: str) -> str:
        return f"{(title or '').strip()}. {(abstract or '').strip()}".strip()

    def _content_hash(self, title: str, abstract: str) -> str:
        base = json.dumps(
            {
                "title": (title or "").strip(),
                "abstract": (abstract or "").strip(),
                "encoder_id": self._encoder_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_embedding(raw: str) -> list[float]:
        value = json.loads(raw)
        if not isinstance(value, list) or not value:
            raise ValueError("Corpus embedding must be a nonempty numeric vector")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
            raise ValueError("Corpus embedding must contain finite numbers")
        return value

    @staticmethod
    def _parse_list(raw: str | None) -> list[str]:
        try:
            value = json.loads(raw or "[]")
            if isinstance(value, list):
                return [str(v) for v in value if str(v).strip()]
        except Exception as _:
            pass
        return []

    @staticmethod
    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b):
            raise ValueError("Corpus embedding dimensions differ; resync the corpus")
        n = len(a)
        if n == 0:
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(n):
            av = float(a[i])
            bv = float(b[i])
            dot += av * bv
            na += av * av
            nb += bv * bv
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / math.sqrt(na * nb)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            if value.endswith("Z"):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    def _engagement_weight(
        self,
        tags: Sequence[str],
        annotation_count: int,
        manual_note_count: int,
        created_at: str | None,
        stale_days_for_weak_negative: int,
    ) -> float:
        signals = self._engagement_signals(
            tags=tags,
            annotation_count=annotation_count,
            manual_note_count=manual_note_count,
            created_at=created_at,
            stale_days_for_weak_negative=stale_days_for_weak_negative,
        )

        if signals["explicit_reject"]:
            return -3.0
        if signals["thumbs_down"]:
            return -2.0
        if signals["explicit_approve"]:
            return 4.0
        if signals["brain"]:
            return 3.0
        if signals["eyes"] or annotation_count > 0:
            return 2.0
        if manual_note_count > 0:
            return 1.5
        return -0.3 if signals["stale_weak_negative"] else 0.0

    def _engagement_signals(
        self,
        tags: Sequence[str],
        annotation_count: int,
        manual_note_count: int,
        created_at: str | None,
        stale_days_for_weak_negative: int,
    ) -> dict[str, object]:
        tags_raw = [str(t or "") for t in tags]
        tags_lower = [t.lower() for t in tags_raw]
        has_explicit_approve = any(TRIAGE_APPROVED_TAG in t for t in tags_raw) or any(
            TRIAGE_APPROVED_TAG_TOKEN in t or FeedbackSignal.EXPLICIT_APPROVE.value in t for t in tags_lower
        )
        has_explicit_reject = any(TRIAGE_REJECTED_TAG in t for t in tags_raw) or any(
            TRIAGE_REJECTED_TAG_TOKEN in t or FeedbackSignal.EXPLICIT_REJECT.value in t for t in tags_lower
        )
        has_brain = any("🧠" in t for t in tags_raw)
        has_eyes = any("👀" in t for t in tags_raw)
        has_thumbsdown = any("👎" in t for t in tags_raw) or any("❌" in t for t in tags_raw)
        has_positive_signal = (
            has_explicit_approve
            or has_brain
            or has_eyes
            or int(annotation_count) > 0
            or int(manual_note_count) > 0
        )

        stale_weak_negative = False
        if not has_positive_signal and not has_explicit_reject and not has_thumbsdown:
            created = self._parse_datetime(created_at)
            if created is not None:
                age_days = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).days
                stale_weak_negative = age_days >= int(stale_days_for_weak_negative)

        return {
            "explicit_approve": has_explicit_approve,
            "explicit_reject": has_explicit_reject,
            "brain": has_brain,
            "eyes": has_eyes,
            "thumbs_down": has_thumbsdown,
            "annotations": int(annotation_count),
            "manual_notes": int(manual_note_count),
            "stale_weak_negative": stale_weak_negative,
            "has_positive_signal": has_positive_signal,
        }
