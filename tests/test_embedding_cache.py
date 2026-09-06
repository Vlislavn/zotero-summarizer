from __future__ import annotations

import json
import pytest

from zotero_summarizer.storage import corpus as embedding_cache
from zotero_summarizer.models import CorpusItem


def test_upsert_items_updates_metadata_without_reembedding(monkeypatch, tmp_path):
    monkeypatch.setattr(embedding_cache, "SentenceTransformer", None)
    cache = embedding_cache.EmbeddingCache(tmp_path / "corpus_cache.db", "test-model")

    embed_calls: list[str] = []

    def fake_embed(text: str) -> list[float]:
        embed_calls.append(text)
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(cache, "_embed", fake_embed)

    initial_item = CorpusItem(
        item_id="paper-1",
        title="Agent Coordination for Clinical Workflows",
        abstract="Studies multiagent coordination.",
        tags=["🧠"],
        collections=["Inbox"],
    )
    imported, updated = cache.upsert_items([initial_item])

    updated_item = initial_item.model_copy(update={"tags": ["🧠", "topic:coordination"], "collections": ["Research > Agents"]})
    imported_2, updated_2 = cache.upsert_items([updated_item])

    assert (imported, updated) == (1, 0)
    assert (imported_2, updated_2) == (0, 1)
    assert len(embed_calls) == 1

    conn = cache._conn()
    try:
        row = conn.execute(
            "SELECT tags_json, collections_json FROM corpus_embeddings WHERE item_id = ?",
            ("paper-1",),
        ).fetchone()
    finally:
        conn.close()

    assert json.loads(row["tags_json"]) == ["topic:coordination", "🧠"]
    assert json.loads(row["collections_json"]) == ["Research > Agents"]


def test_encoding_error_propagates(monkeypatch, tmp_path):
    cache = embedding_cache.EmbeddingCache(tmp_path / "corpus_cache.db", "test-model")
    model = cache._load_model()
    def fail(*args, **kwargs):
        raise RuntimeError("encode failed")
    monkeypatch.setattr(model, "encode", fail)
    with pytest.raises(RuntimeError, match="encode failed"):
        cache._embed("agent safety policy enforcement")


def test_match_candidate_applies_positive_and_negative_feedback(monkeypatch, tmp_path):
    monkeypatch.setattr(embedding_cache, "SentenceTransformer", None)
    cache = embedding_cache.EmbeddingCache(tmp_path / "corpus_cache.db", "test-model")

    vectors = {
        "Agent Coordination for Clinical Workflows. Studies multiagent coordination.": [1.0, 0.0, 0.0],
        "Rejected Coordination Paper. Also about multiagent coordination.": [1.0, 0.0, 0.0],
        "Candidate Paper. Studies multiagent coordination.": [1.0, 0.0, 0.0],
        "goal:agentic": [1.0, 0.0, 0.0],
    }

    def fake_embed(text: str) -> list[float]:
        return vectors.get(text, [0.0, 1.0, 0.0])

    monkeypatch.setattr(cache, "_embed", fake_embed)
    cache.upsert_goals(["goal:agentic"])
    cache.upsert_items(
        [
            CorpusItem(
                item_id="positive",
                title="Agent Coordination for Clinical Workflows",
                abstract="Studies multiagent coordination.",
                tags=["🧠"],
                collections=["Research > Agents"],
            ),
            CorpusItem(
                item_id="negative",
                title="Rejected Coordination Paper",
                abstract="Also about multiagent coordination.",
                tags=["❌"],
                collections=["Rejected"],
            ),
        ]
    )

    result = cache.match_candidate("Candidate Paper", "Studies multiagent coordination.")

    assert result.has_corpus
    assert result.positive_similarity == 1.0
    assert result.negative_similarity == 1.0
    assert result.affinity_score == 0.0
    assert result.suggested_collections == ["Research > Agents"]
    assert result.matched_goal == "goal:agentic"


def test_affinity_and_goals_matches_match_candidate(monkeypatch, tmp_path):
    """The vectorized fast path returns the same affinity as match_candidate."""
    monkeypatch.setattr(embedding_cache, "SentenceTransformer", None)
    cache = embedding_cache.EmbeddingCache(tmp_path / "corpus_cache.db", "test-model")

    vectors = {
        "Agent Coordination for Clinical Workflows. Studies multiagent coordination.": [1.0, 0.0, 0.0],
        "Rejected Coordination Paper. Also about multiagent coordination.": [0.0, 1.0, 0.0],
        "Candidate Paper. Studies multiagent coordination.": [1.0, 0.0, 0.0],
    }
    monkeypatch.setattr(cache, "_embed", lambda t: vectors.get(t, [0.0, 0.0, 1.0]))
    cache.upsert_items([
        CorpusItem(item_id="positive", title="Agent Coordination for Clinical Workflows",
                   abstract="Studies multiagent coordination.", tags=["🧠"], collections=["A"]),
        CorpusItem(item_id="negative", title="Rejected Coordination Paper",
                   abstract="Also about multiagent coordination.", tags=["❌"], collections=["R"]),
    ])

    full = cache.match_candidate("Candidate Paper", "Studies multiagent coordination.").affinity_score
    fast, _goal_sims = cache.affinity_and_goals("Candidate Paper", "Studies multiagent coordination.")
    assert fast == full
    # candidate==positive vector, negative orthogonal → pos_sim 1, neg_sim 0 → affinity 1.0
    assert fast == 1.0


def test_affinity_cache_invalidates_on_upsert(monkeypatch, tmp_path):
    """Adding a corpus item changes the fingerprint → the matrix rebuilds."""
    monkeypatch.setattr(embedding_cache, "SentenceTransformer", None)
    cache = embedding_cache.EmbeddingCache(tmp_path / "corpus_cache.db", "test-model")
    monkeypatch.setattr(cache, "_embed", lambda t: [1.0, 0.0, 0.0])

    cache.upsert_items([CorpusItem(item_id="p1", title="A", abstract="x", tags=["🧠"], collections=[])])
    a1, _ = cache.affinity_and_goals("cand", "y")  # builds the cache; only a positive → 1.0
    assert a1 == 1.0
    matrix = cache._affinity_cache["matrix"]

    cache.upsert_items([CorpusItem(item_id="p2", title="B", abstract="z", tags=["❌"], collections=[])])
    a2, _ = cache.affinity_and_goals("cand", "y")  # rebuilt with the strong negative → 0.0
    assert cache._affinity_cache["matrix"] is not matrix
    assert a2 == 0.0
