"""Read/query side of EmbeddingCache (match + metadata).

Mixed into EmbeddingCache; methods use ``self`` helpers from that class.
"""
from __future__ import annotations

import sqlite3  # noqa: F401  (type hints)

import numpy as np

from zotero_summarizer.storage.corpus_types import CorpusMatchResult


class CorpusReadMixin:

    def _corpus_arrays(self, stale_days: int) -> tuple[np.ndarray, np.ndarray]:
        """``(matrix, weights)`` for the corpus, cached until the corpus changes.

        ``matrix``: (N, dim) float32 normalized embeddings; ``weights``: (N,)
        engagement weights for ``stale_days``. Parsing the (large) embedding set
        is the expensive part — done once and reused across scored items, rebuilt
        only when ``_corpus_version`` (bumped on any corpus write) or
        ``stale_days`` changes."""
        cache = self._affinity_cache
        if (
            cache is not None
            and cache["version"] == self._corpus_version
            and cache["stale_days"] == stale_days
        ):
            return cache["matrix"], cache["weights"]
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT tags_json, collections_json, annotation_count, manual_note_count, "
                "created_at, embedding_json FROM corpus_embeddings"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            matrix = np.zeros((0, self._vector_dim()), dtype=np.float32)
            weights = np.zeros((0,), dtype=np.float32)
        else:
            matrix = np.asarray(
                [self._parse_embedding(r["embedding_json"]) for r in rows], dtype=np.float32
            )
            weights = np.asarray(
                [
                    self._engagement_weight(
                        tags=self._parse_list(r["tags_json"]),
                        annotation_count=int(r["annotation_count"] or 0),
                        manual_note_count=int(r["manual_note_count"] or 0),
                        created_at=r["created_at"],
                        stale_days_for_weak_negative=stale_days,
                    )
                    for r in rows
                ],
                dtype=np.float32,
            )
        self._affinity_cache = {
            "version": self._corpus_version,
            "stale_days": stale_days,
            "matrix": matrix,
            "weights": weights,
        }
        return matrix, weights

    def affinity_only(
        self, title: str, abstract: str, stale_days_for_weak_negative: int = 30
    ) -> float:
        """Fast corpus affinity (``positive_similarity - negative_similarity``) —
        the gate's per-item feature, vectorized over the cached corpus matrix.

        Identical math to :meth:`match_candidate`'s affinity but ~1000× cheaper at
        scale (one numpy matmul vs a Python cosine loop over the whole corpus with
        a JSON re-parse each call). The full ``match_candidate`` (collections /
        goals / top items) stays for the review UI."""
        matrix, weights = self._corpus_arrays(stale_days_for_weak_negative)
        if matrix.shape[0] == 0:
            return 0.0
        cand = np.asarray(self._embed(self._build_text(title, abstract)), dtype=np.float32)
        norm = float(np.linalg.norm(cand))
        if norm > 0:
            cand = cand / norm
        sims = matrix @ cand
        pos = weights > 0
        neg = weights < 0
        pos_den = float(weights[pos].sum())
        neg_w = -weights[neg]
        neg_den = float(neg_w.sum())
        positive_similarity = float((sims[pos] * weights[pos]).sum() / pos_den) if pos_den > 0 else 0.0
        negative_similarity = float((sims[neg] * neg_w).sum() / neg_den) if neg_den > 0 else 0.0
        affinity = self._clamp(positive_similarity - negative_similarity, -1.0, 1.0)
        return round(affinity, 4)

    def _affinity_to_targets(self, item_ids: list[str], target_mat: np.ndarray) -> dict[str, float]:
        """``{item_id: max cosine to the rows of target_mat}`` over the items'
        ALREADY-CACHED corpus embeddings (no model load, no re-embed).

        ``target_mat`` is a ``(G×dim)`` array of ALREADY-L2-normalized target
        vectors — the research-goal embeddings (``goal_affinity_for_items``) or a
        ``1×dim`` query vector (``query_affinity_for_items``). One IN-query + one
        ``(n×dim)·(dim×G)`` matmul → per-item max cosine; items with no cached
        embedding or a zero-norm vector are omitted (caller falls back)."""
        ids = [str(i) for i in item_ids if str(i or "").strip()]
        if not ids or target_mat.shape[0] == 0:
            return {}
        conn = self._conn()
        try:
            placeholders = ",".join("?" * len(ids))
            item_rows = conn.execute(
                f"SELECT item_id, embedding_json FROM corpus_embeddings WHERE item_id IN ({placeholders})",
                ids,
            ).fetchall()
        finally:
            conn.close()
        if not item_rows:
            return {}
        ids_out = [str(r["item_id"]) for r in item_rows]
        mat = np.asarray([self._parse_embedding(r["embedding_json"]) for r in item_rows], dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        best = (mat / np.where(norms > 0, norms, 1.0) @ target_mat.T).max(axis=1)
        valid = norms[:, 0] > 0
        return {ids_out[i]: float(best[i]) for i in range(len(ids_out)) if valid[i]}

    def goal_affinity_for_items(self, item_ids: list[str]) -> dict[str, float]:
        """``{item_id: max cosine to the research-goal embeddings}`` — the
        goal-anchored relevance signal.

        Uses ALREADY-CACHED corpus embeddings (no model load, no re-embed), so it
        is cheap at queue-build time even for the whole library: one IN-query +
        a (n×dim)·(dim×G) matmul. Items with no cached embedding, or when no goals
        are set, are omitted (caller falls back to the gate-only order). Distinct
        from :meth:`affinity_only` (engagement-based pos−neg) — this is similarity
        to what the user SAID they want, which the gate does not feature."""
        if not [i for i in item_ids if str(i or "").strip()]:
            return {}
        conn = self._conn()
        try:
            goal_rows = conn.execute("SELECT embedding_json FROM goal_embeddings").fetchall()
        finally:
            conn.close()
        if not goal_rows:
            return {}
        gmat = np.asarray([self._parse_embedding(r["embedding_json"]) for r in goal_rows], dtype=np.float32)
        gnorms = np.linalg.norm(gmat, axis=1, keepdims=True)
        gmat = gmat / np.where(gnorms > 0, gnorms, 1.0)
        return self._affinity_to_targets(item_ids, gmat)

    def query_affinity_for_items(self, query: str, item_ids: list[str]) -> dict[str, float]:
        """``{item_id: cosine of the item's cached embedding to the QUERY text}``
        — ad-hoc semantic-search relevance (the dense leg of Library hybrid
        search). Embeds the query ONCE via the resident model, then reuses the
        cached-corpus matmul (``_affinity_to_targets``). Distinct from
        :meth:`goal_affinity_for_items`: similarity to a search string, not the
        stored research goals. Empty query / unembeddable → ``{}``."""
        q = str(query or "").strip()
        if not q:
            return {}
        qvec = np.asarray(self._embed(q), dtype=np.float32)
        n = float(np.linalg.norm(qvec))
        if n == 0:
            return {}
        qmat = (qvec / n).reshape(1, -1)
        return self._affinity_to_targets(item_ids, qmat)

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
