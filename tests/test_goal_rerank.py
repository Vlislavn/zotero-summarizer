"""Goal-aware re-rank: blend goal-text similarity into the queue order so
on-goal papers the gate under-ranks float up (banding stays from the gate)."""
from __future__ import annotations

import json

from zotero_summarizer.services.library import _ranking
from zotero_summarizer.storage.corpus import EmbeddingCache


def _rec(key, rel, goal):
    return {"item_key": key, "relevance_score": rel, "goal_sim": goal, "date_added": "2026-05-01"}


def test_blended_sort_floats_on_goal_item_above_higher_relevance():
    # item B has mid relevance but the strongest goal match; with the 0.4 goal
    # weight it should outrank A (highest relevance, off-goal). C (low both) last.
    recs = [_rec("A", 4.0, 0.0), _rec("B", 3.0, 0.5), _rec("C", 2.0, 0.1)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A", "C"]


def test_blended_sort_unscored_sink_to_bottom():
    recs = [_rec("A", None, 0.9), _rec("B", 3.0, 0.0)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A"]


def test_blended_sort_no_goal_signal_falls_back_to_relevance():
    recs = [_rec("A", 2.0, None), _rec("B", 4.0, None)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A"]  # pure relevance order


def test_goal_affinity_for_items_returns_max_cosine(tmp_path):
    cache = EmbeddingCache(tmp_path / "corpus.db", "stub-model")
    conn = cache._conn()
    try:
        # Two goals (unit axes) + items aligned to each.
        conn.execute("INSERT INTO goal_embeddings (goal, embedding_json) VALUES (?, ?)",
                     ("agents", json.dumps([1.0, 0.0, 0.0])))
        conn.execute("INSERT INTO goal_embeddings (goal, embedding_json) VALUES (?, ?)",
                     ("clinical", json.dumps([0.0, 1.0, 0.0])))
        for iid, vec in [("on_goal", [0.0, 1.0, 0.0]), ("off_goal", [0.0, 0.0, 1.0])]:
            conn.execute(
                "INSERT INTO corpus_embeddings (item_id, title, content_hash, embedding_json) VALUES (?,?,?,?)",
                (iid, iid, "h", json.dumps(vec)),
            )
        conn.commit()
    finally:
        conn.close()
    out = cache.goal_affinity_for_items(["on_goal", "off_goal", "missing"])
    assert out["on_goal"] == 1.0          # exactly matches the "clinical" goal axis
    assert abs(out["off_goal"]) < 1e-6     # orthogonal to both goals
    assert "missing" not in out            # no cached embedding → omitted
