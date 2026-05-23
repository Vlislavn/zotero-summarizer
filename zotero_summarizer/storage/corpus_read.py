"""Read/query side of EmbeddingCache (match + metadata).

Mixed into EmbeddingCache; methods use ``self`` helpers from that class.
"""
from __future__ import annotations

import sqlite3  # noqa: F401  (type hints)

from zotero_summarizer.storage.corpus_types import CorpusMatchResult


class CorpusReadMixin:

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
