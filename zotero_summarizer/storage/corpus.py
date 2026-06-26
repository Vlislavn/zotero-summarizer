from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
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
from zotero_summarizer.storage.corpus_types import EMBEDDING_DIM, CorpusMatchResult  # noqa: F401

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional dependency fallback
    SentenceTransformer = None


LOGGER = logging.getLogger("zotero_summarizer.embedding_cache")


class EmbeddingCache(CorpusReadMixin):
    """Stores and queries library embeddings for corpus-aware triage.

    Read/query methods (match + metadata) live in ``CorpusReadMixin``.
    """

    def __init__(self, db_path: Path, model_name: str) -> None:
        self.db_path = db_path
        self.model_name = model_name
        self._model = None
        self._dim = None
        self._model_load_attempted = False
        self._warned_fallback = False
        # SentenceTransformer/torch inference is not safe to call from multiple
        # threads on one shared model; the backlog drain scores survivors on a
        # thread pool, so serialize the embedding forward pass. Only the fast
        # torch step is guarded — the slow LLM HTTP calls still overlap.
        self._embed_lock = threading.Lock()
        # Cached corpus matrix for the vectorized affinity_and_goals() fast path:
        # {version, stale_days, matrix (np float32 N×dim), weights (np N)}.
        # Rebuilt when _corpus_version changes (bumped on any corpus write) so we
        # parse the (large) embedding set once, not per scored item.
        self._affinity_cache: dict[str, Any] | None = None
        self._corpus_version = 0
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as _:
            pass
        return conn

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

    def upsert_goals(self, goals: Sequence[str]) -> None:
        normalized_goals = sorted({str(goal or "").strip() for goal in goals if str(goal or "").strip()})
        conn = self._conn()
        try:
            for goal_text in normalized_goals:
                embedding = self._embed(goal_text)
                conn.execute(
                    """
                    INSERT INTO goal_embeddings (goal, embedding_json)
                    VALUES (?, ?)
                    ON CONFLICT(goal) DO UPDATE SET
                        embedding_json = excluded.embedding_json,
                        updated_at = datetime('now')
                    """,
                    (goal_text, json.dumps(embedding)),
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
            self._corpus_version += 1  # invalidate the cached affinity matrix
            return int(row["total"] or 0) if row else 0
        finally:
            conn.close()

    def upsert_items(self, items: Sequence[CorpusItem]) -> tuple[int, int]:
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
                    SELECT title, abstract, tags_json, collections_json, annotation_count,
                           manual_note_count, created_at, content_hash
                    FROM corpus_embeddings
                    WHERE item_id = ?
                    """,
                    (item.item_id,),
                ).fetchone()

                if existing:
                    metadata_unchanged = (
                        str(existing["title"] or "") == item.title
                        and str(existing["abstract"] or "") == item.abstract
                        and str(existing["tags_json"] or "[]") == tags_json
                        and str(existing["collections_json"] or "[]") == collections_json
                        and int(existing["annotation_count"] or 0) == int(item.annotation_count)
                        and int(existing["manual_note_count"] or 0) == int(item.manual_note_count)
                        and str(existing["created_at"] or "") == str(item.created_at or "")
                    )
                    if existing["content_hash"] == content_hash and metadata_unchanged:
                        continue

                    if existing["content_hash"] == content_hash:
                        conn.execute(
                            """
                            UPDATE corpus_embeddings
                            SET title = ?,
                                abstract = ?,
                                tags_json = ?,
                                collections_json = ?,
                                annotation_count = ?,
                                manual_note_count = ?,
                                created_at = ?,
                                updated_at = datetime('now')
                            WHERE item_id = ?
                            """,
                            (
                                item.title,
                                item.abstract,
                                tags_json,
                                collections_json,
                                int(item.annotation_count),
                                int(item.manual_note_count),
                                item.created_at,
                                item.item_id,
                            ),
                        )
                        updated += 1
                        continue

                embedding = self._embed(text)
                conn.execute(
                    """
                    INSERT INTO corpus_embeddings (
                        item_id, title, abstract, tags_json, collections_json,
                        annotation_count, manual_note_count, created_at,
                        content_hash, embedding_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        title = excluded.title,
                        abstract = excluded.abstract,
                        tags_json = excluded.tags_json,
                        collections_json = excluded.collections_json,
                        annotation_count = excluded.annotation_count,
                        manual_note_count = excluded.manual_note_count,
                        created_at = excluded.created_at,
                        content_hash = excluded.content_hash,
                        embedding_json = excluded.embedding_json,
                        updated_at = datetime('now')
                    """,
                    (
                        item.item_id,
                        item.title,
                        item.abstract,
                        tags_json,
                        collections_json,
                        int(item.annotation_count),
                        int(item.manual_note_count),
                        item.created_at,
                        content_hash,
                        json.dumps(embedding),
                    ),
                )
                if existing:
                    updated += 1
                else:
                    imported += 1
            conn.commit()
        finally:
            conn.close()
        if imported or updated:
            self._corpus_version += 1  # invalidate the cached affinity matrix
        return imported, updated

    def _load_model(self):
        if self._model_load_attempted:
            return self._model
        self._model_load_attempted = True
        if self._model is not None:
            return self._model
        if SentenceTransformer is None:
            LOGGER.warning("sentence-transformers is unavailable; corpus matching will fall back to hashed embeddings")
            return None
        LOGGER.info("Loading embedding model: %s", self.model_name)
        try:
            self._model = SentenceTransformer(self.model_name)
            # sentence-transformers renamed the API; prefer the new name
            # when available and fall back to the old one for older
            # installations. Both are documented as the canonical way to
            # read the embedding dimension.
            if hasattr(self._model, "get_embedding_dimension"):
                self._dim = int(self._model.get_embedding_dimension() or 0) or None
            elif hasattr(self._model, "get_sentence_embedding_dimension"):
                self._dim = int(self._model.get_sentence_embedding_dimension() or 0) or None
        except Exception:
            LOGGER.exception("Failed to load embedding model: %s", self.model_name)
            self._model = None
        return self._model

    def _embed(self, text: str) -> list[float]:
        cleaned = text.strip()
        if not cleaned:
            return [0.0] * self._vector_dim()

        with self._embed_lock:  # torch encode is not thread-safe (see __init__)
            model = self._load_model()
            if model is not None:
                vector = model.encode(cleaned, normalize_embeddings=True)
                if hasattr(vector, "tolist"):
                    values = [float(v) for v in vector.tolist()]
                else:
                    values = [float(v) for v in vector]
                self._dim = len(values) or self._dim
                return values
        if not self._warned_fallback:
            LOGGER.warning("Using hashed fallback embeddings; corpus similarity quality will be degraded")
            self._warned_fallback = True
        return self._fallback_embedding(cleaned)

    def _fallback_embedding(self, text: str, dim: int | None = None) -> list[float]:
        dim = dim or self._vector_dim()
        vector = [0.0] * dim
        tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            idx = int(digest, 16) % dim
            vector[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            return vector
        return [x / norm for x in vector]

    @staticmethod
    def _build_text(title: str, abstract: str) -> str:
        return f"{(title or '').strip()}. {(abstract or '').strip()}".strip()

    @staticmethod
    def _content_hash(title: str, abstract: str) -> str:
        base = json.dumps(
            {
                "title": (title or "").strip(),
                "abstract": (abstract or "").strip(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_embedding(raw: str) -> list[float]:
        try:
            value = json.loads(raw or "[]")
            if isinstance(value, list):
                return [float(v) for v in value]
        except Exception as _:
            pass
        return [0.0] * EMBEDDING_DIM

    def _vector_dim(self) -> int:
        return int(self._dim or EMBEDDING_DIM)

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
        n = min(len(a), len(b))
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

        has_explicit_approve = bool(signals["explicit_approve"])
        has_explicit_reject = bool(signals["explicit_reject"])
        has_brain = bool(signals["brain"])
        has_eyes = bool(signals["eyes"])
        has_thumbsdown = bool(signals["thumbs_down"])

        if has_explicit_reject:
            return -3.0

        if has_thumbsdown:
            return -2.0

        weight = 1.0
        if has_explicit_approve:
            weight = max(weight, 4.0)
        if has_brain:
            weight = max(weight, 3.0)
        if has_eyes:
            weight = max(weight, 2.0)
        if annotation_count > 0:
            weight = max(weight, 2.0)
        if manual_note_count > 0:
            weight = max(weight, 1.5)

        has_signal = bool(signals["has_positive_signal"])
        if not has_signal:
            if bool(signals["stale_weak_negative"]):
                return 0.3
            return 0.0

        return weight

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
