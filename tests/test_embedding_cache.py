from __future__ import annotations

import json

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


def test_fallback_embeddings_use_model_dimension(monkeypatch, tmp_path):
    monkeypatch.setattr(embedding_cache, "SentenceTransformer", None)
    cache = embedding_cache.EmbeddingCache(tmp_path / "corpus_cache.db", "test-model")

    vector = cache._embed("agent safety policy enforcement")

    assert len(vector) == 384
    assert sum(abs(value) for value in vector) > 0


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
