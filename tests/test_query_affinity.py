"""query_affinity_for_items (dense leg of hybrid search) + the _affinity_to_targets
refactor regression guard. Model-free: corpus embeddings are inserted directly and
the query embed is monkeypatched, so these are deterministic and fast."""
from __future__ import annotations

import json

from zotero_summarizer.storage.corpus import EmbeddingCache


def _cache_with_items(tmp_path, items: dict[str, list[float]]) -> EmbeddingCache:
    cache = EmbeddingCache(tmp_path / "c.db", "fake-model")
    conn = cache._conn()
    try:
        for iid, vec in items.items():
            conn.execute(
                "INSERT INTO corpus_embeddings (item_id, title, abstract, content_hash, embedding_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (iid, f"T{iid}", "abs", "h", json.dumps(vec)),
            )
        conn.commit()
    finally:
        conn.close()
    return cache


def test_query_affinity_ranks_on_topic_higher(tmp_path, monkeypatch):
    cache = _cache_with_items(tmp_path, {"A": [1.0, 0.0], "B": [0.0, 1.0]})
    # Query embeds onto A's axis → A is the on-topic hit.
    monkeypatch.setattr(cache, "_embed", lambda text: [1.0, 0.0])
    out = cache.query_affinity_for_items("hallucination mitigation", ["A", "B"])
    assert out["A"] > out["B"]
    assert round(out["A"], 4) == 1.0
    assert round(out["B"], 4) == 0.0


def test_query_affinity_empty_query_is_empty(tmp_path):
    cache = _cache_with_items(tmp_path, {"A": [1.0, 0.0]})
    assert cache.query_affinity_for_items("", ["A"]) == {}
    assert cache.query_affinity_for_items("   ", ["A"]) == {}


def test_query_affinity_omits_items_without_embedding(tmp_path, monkeypatch):
    cache = _cache_with_items(tmp_path, {"A": [1.0, 0.0]})
    monkeypatch.setattr(cache, "_embed", lambda text: [1.0, 0.0])
    out = cache.query_affinity_for_items("x", ["A", "MISSING"])
    assert "A" in out and "MISSING" not in out


def test_goal_affinity_unchanged_after_refactor(tmp_path):
    """Regression guard: the _affinity_to_targets extraction must leave
    goal_affinity_for_items byte-identical."""
    cache = _cache_with_items(tmp_path, {"A": [1.0, 0.0], "B": [0.0, 1.0]})
    conn = cache._conn()
    try:
        conn.execute(
            "INSERT INTO goal_embeddings (goal, embedding_json) VALUES (?, ?)",
            ("g", json.dumps([1.0, 0.0])),
        )
        conn.commit()
    finally:
        conn.close()
    out = cache.goal_affinity_for_items(["A", "B"])
    assert round(out["A"], 4) == 1.0
    assert round(out["B"], 4) == 0.0
    # No goals → empty (caller falls back to gate order).
    cache2 = _cache_with_items(tmp_path / "x", {"A": [1.0, 0.0]})
    assert cache2.goal_affinity_for_items(["A"]) == {}


def test_normalized_corpus_matrix_cached_until_corpus_changes(tmp_path):
    """The process-wide matrix cache (fix C) must reuse the parsed embeddings
    across calls — the reading queue builds a fresh EmbeddingCache per open — and
    rebuild only when the corpus actually changes."""
    cache = _cache_with_items(tmp_path, {"A": [1.0, 0.0], "B": [0.0, 1.0]})
    _, mat1, _ = cache._normalized_corpus_matrix()
    _, mat2, _ = cache._normalized_corpus_matrix()
    assert mat2 is mat1  # cache HIT → exact same object, no re-parse

    conn = cache._conn()
    try:
        conn.execute(
            "INSERT INTO corpus_embeddings (item_id, title, content_hash, embedding_json) VALUES (?,?,?,?)",
            ("C", "C", "h", json.dumps([1.0, 1.0])),
        )
        conn.commit()
    finally:
        conn.close()
    index3, mat3, _ = cache._normalized_corpus_matrix()
    assert mat3 is not mat1  # corpus changed → rebuilt
    assert "C" in index3
