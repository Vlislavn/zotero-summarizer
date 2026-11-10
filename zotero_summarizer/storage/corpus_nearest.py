"""Nearest *kept* paper per item (mixin of ``EmbeddingCache``).

Powers the Read-next row reason "close to: <title>". Reuses the process-wide
normalized corpus matrix, so no model load and no re-parse of embeddings.
"""
from __future__ import annotations

import numpy as np


def _norm(title: object) -> str:
    return " ".join(str(title or "").lower().split())


class CorpusNearestMixin:

    def nearest_kept_titles(self, item_ids: list[str]) -> dict[str, str]:
        """``{item_id: title of the most similar kept paper}`` (kept = positive
        engagement weight, never the item itself). Items without a cached
        embedding are omitted; no kept paper at all → ``{}``."""
        ids = list(dict.fromkeys(str(i) for i in item_ids if str(i or "").strip()))
        index, mat, valid = self._normalized_corpus_matrix()
        ids = [i for i in ids if i in index and valid[index[i]]]
        if not ids:
            return {}
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT item_id, title, tags_json, annotation_count, manual_note_count, created_at "
                "FROM corpus_embeddings"
            ).fetchall()
        finally:
            conn.close()
        kept = [
            r for r in rows
            if r["item_id"] in index and valid[index[r["item_id"]]] and self._engagement_weight(
                tags=self._parse_list(r["tags_json"]),
                annotation_count=int(r["annotation_count"] or 0),
                manual_note_count=int(r["manual_note_count"] or 0),
                created_at=r["created_at"],
                stale_days_for_weak_negative=30,
            ) > 0
        ]
        if not kept:
            return {}
        kept_rows = [index[r["item_id"]] for r in kept]
        sims = mat[[index[i] for i in ids]] @ mat[kept_rows].T
        # Never "close to" itself: same item, or another copy of the same paper (same title).
        columns: dict[str, list[int]] = {}
        for j, kept_row in enumerate(kept):
            columns.setdefault(_norm(kept_row["title"]), []).append(j)
            columns.setdefault("#" + kept_row["item_id"], []).append(j)
        titles = {r["item_id"]: _norm(r["title"]) for r in rows}
        for i, item_id in enumerate(ids):
            for j in columns.get(titles[item_id], []) + columns.get("#" + item_id, []):
                sims[i, j] = -np.inf
        best = sims.argmax(axis=1)
        return {
            item_id: str(kept[best[i]]["title"] or "").strip()
            for i, item_id in enumerate(ids)
            if np.isfinite(sims[i, best[i]]) and str(kept[best[i]]["title"] or "").strip()
        }
