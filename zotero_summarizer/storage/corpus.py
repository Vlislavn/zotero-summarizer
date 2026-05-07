from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from zotero_summarizer.domain import (
    FeedbackSignal,
    TRIAGE_APPROVED_TAG,
    TRIAGE_APPROVED_TAG_TOKEN,
    TRIAGE_REJECTED_TAG,
    TRIAGE_REJECTED_TAG_TOKEN,
)
from zotero_summarizer.models import CorpusItem

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional dependency fallback
    SentenceTransformer = None


LOGGER = logging.getLogger("zotero_summarizer.embedding_cache")
EMBEDDING_DIM = 384


@dataclass
class CorpusMatchResult:
    has_corpus: bool
    affinity_score: float
    positive_similarity: float
    negative_similarity: float
    matched_goal: str
    matched_goal_similarity: float
    suggested_collections: list[str]
    top_similar_items: list[str]


class EmbeddingCache:
    """Stores and queries library embeddings for corpus-aware triage."""

    def __init__(self, db_path: Path, model_name: str) -> None:
        self.db_path = db_path
        self.model_name = model_name
        self._model = None
        self._dim = None
        self._model_load_attempted = False
        self._warned_fallback = False
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
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
        return imported, updated

    def match_candidate(
        self,
        title: str,
        abstract: str,
        stale_days_for_weak_negative: int = 30,
        top_k: int = 3,
    ) -> CorpusMatchResult:
        candidate_embedding = self._embed(self._build_text(title, abstract))

        conn = self._conn()
        try:
            corpus_rows = conn.execute(
                """
                SELECT item_id, title, tags_json, collections_json, annotation_count,
                       manual_note_count, created_at, embedding_json
                FROM corpus_embeddings
                """
            ).fetchall()
            goal_rows = conn.execute("SELECT goal, embedding_json FROM goal_embeddings").fetchall()
        finally:
            conn.close()

        if not corpus_rows:
            return CorpusMatchResult(
                has_corpus=False,
                affinity_score=0.0,
                positive_similarity=0.0,
                negative_similarity=0.0,
                matched_goal="",
                matched_goal_similarity=0.0,
                suggested_collections=[],
                top_similar_items=[],
            )

        scored_rows: list[tuple[float, sqlite3.Row, float]] = []
        pos_num = 0.0
        pos_den = 0.0
        neg_num = 0.0
        neg_den = 0.0
        collection_num: dict[str, float] = {}
        collection_den: dict[str, float] = {}

        for row in corpus_rows:
            embedding = self._parse_embedding(row["embedding_json"])
            sim = self._cosine(candidate_embedding, embedding)
            weight = self._engagement_weight(
                tags=self._parse_list(row["tags_json"]),
                annotation_count=int(row["annotation_count"] or 0),
                manual_note_count=int(row["manual_note_count"] or 0),
                created_at=row["created_at"],
                stale_days_for_weak_negative=stale_days_for_weak_negative,
            )
            scored_rows.append((sim, row, weight))

            if weight > 0:
                pos_num += sim * weight
                pos_den += weight
                collections = self._parse_list(row["collections_json"])
                for collection_name in collections:
                    if not collection_name:
                        continue
                    collection_num[collection_name] = collection_num.get(collection_name, 0.0) + sim * weight
                    collection_den[collection_name] = collection_den.get(collection_name, 0.0) + weight
            elif weight < 0:
                w = abs(weight)
                neg_num += sim * w
                neg_den += w

        positive_similarity = pos_num / pos_den if pos_den > 0 else 0.0
        negative_similarity = neg_num / neg_den if neg_den > 0 else 0.0
        affinity = self._clamp(positive_similarity - negative_similarity, -1.0, 1.0)

        top_titles = [
            str(row[1]["title"])
            for row in sorted(scored_rows, key=lambda x: x[0], reverse=True)[:top_k]
            if row[0] > 0
        ]

        collection_scores: list[tuple[str, float]] = []
        for name, num in collection_num.items():
            den = collection_den.get(name, 0.0)
            if den <= 0:
                continue
            collection_scores.append((name, num / den))
        collection_scores.sort(key=lambda x: x[1], reverse=True)
        suggested_collections = [name for name, _ in collection_scores[:3]]

        matched_goal = ""
        matched_goal_similarity = 0.0
        for row in goal_rows:
            score = self._cosine(candidate_embedding, self._parse_embedding(row["embedding_json"]))
            if score > matched_goal_similarity:
                matched_goal_similarity = score
                matched_goal = str(row["goal"])

        return CorpusMatchResult(
            has_corpus=True,
            affinity_score=round(affinity, 4),
            positive_similarity=round(positive_similarity, 4),
            negative_similarity=round(negative_similarity, 4),
            matched_goal=matched_goal,
            matched_goal_similarity=round(matched_goal_similarity, 4),
            suggested_collections=suggested_collections,
            top_similar_items=top_titles,
        )

    def get_item_metadata(
        self,
        item_id: str,
        stale_days_for_weak_negative: int = 30,
    ) -> dict[str, object] | None:
        safe_item_id = str(item_id or "").strip()
        if not safe_item_id:
            return None

        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT item_id, title, abstract, tags_json, collections_json,
                       annotation_count, manual_note_count, created_at, updated_at
                FROM corpus_embeddings
                WHERE item_id = ?
                LIMIT 1
                """,
                (safe_item_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None
        return self._metadata_from_row(row, stale_days_for_weak_negative)

    def list_items_metadata(
        self,
        limit: int = 200,
        offset: int = 0,
        search: str | None = None,
        stale_days_for_weak_negative: int = 30,
        sort_by: str = "updated_at",
        order: str = "desc",
    ) -> dict[str, object]:
        safe_limit = max(1, min(int(limit), 1000))
        safe_offset = max(0, int(offset))
        safe_search = str(search or "").strip()
        safe_sort_by = sort_by if sort_by in {"updated_at", "created_at", "title", "item_id"} else "updated_at"
        safe_order = "asc" if str(order or "").lower() == "asc" else "desc"

        where_sql = ""
        params: list[object] = []
        if safe_search:
            where_sql = " WHERE lower(item_id) LIKE ? OR lower(title) LIKE ? "
            token = f"%{safe_search.lower()}%"
            params.extend([token, token])

        conn = self._conn()
        try:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS total FROM corpus_embeddings{where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT item_id, title, abstract, tags_json, collections_json,
                       annotation_count, manual_note_count, created_at, updated_at
                FROM corpus_embeddings
                {where_sql}
                ORDER BY {safe_sort_by} {safe_order}
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        finally:
            conn.close()

        items = [self._metadata_from_row(row, stale_days_for_weak_negative) for row in rows]
        total = int(total_row["total"] or 0) if total_row else 0
        return {
            "total": total,
            "items": items,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def _metadata_from_row(self, row: sqlite3.Row, stale_days_for_weak_negative: int) -> dict[str, object]:
        tags = self._parse_list(row["tags_json"])
        collections = self._parse_list(row["collections_json"])
        annotation_count = int(row["annotation_count"] or 0)
        manual_note_count = int(row["manual_note_count"] or 0)
        created_at = str(row["created_at"] or "") or None
        signals = self._engagement_signals(
            tags=tags,
            annotation_count=annotation_count,
            manual_note_count=manual_note_count,
            created_at=created_at,
            stale_days_for_weak_negative=stale_days_for_weak_negative,
        )
        engagement_weight = self._engagement_weight(
            tags=tags,
            annotation_count=annotation_count,
            manual_note_count=manual_note_count,
            created_at=created_at,
            stale_days_for_weak_negative=stale_days_for_weak_negative,
        )
        return {
            "item_id": str(row["item_id"] or ""),
            "title": str(row["title"] or ""),
            "abstract": str(row["abstract"] or ""),
            "tags": tags,
            "collections": collections,
            "annotation_count": annotation_count,
            "manual_note_count": manual_note_count,
            "created_at": created_at,
            "updated_at": str(row["updated_at"] or "") or None,
            "engagement_weight": round(float(engagement_weight), 3),
            "signals": signals,
        }

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
            if hasattr(self._model, "get_sentence_embedding_dimension"):
                self._dim = int(self._model.get_sentence_embedding_dimension() or 0) or None
        except Exception:
            LOGGER.exception("Failed to load embedding model: %s", self.model_name)
            self._model = None
        return self._model

    def _embed(self, text: str) -> list[float]:
        cleaned = text.strip()
        if not cleaned:
            return [0.0] * self._vector_dim()

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
        except Exception:
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
        except Exception:
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
