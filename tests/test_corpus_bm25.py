"""CorpusBM25 (lexical leg of hybrid search): ranking, candidate restriction,
empty query, cache rebuild on corpus change, and rerank text fetch."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("rank_bm25")  # skip if the optional dep isn't installed yet

from zotero_summarizer.storage import corpus_bm25
from zotero_summarizer.storage.corpus import EmbeddingCache


def _seed_corpus(tmp_path, docs: dict[str, tuple[str, str]]):
    db = tmp_path / "c.db"
    EmbeddingCache(db, "fake")  # creates the corpus_embeddings schema
    conn = sqlite3.connect(str(db))
    try:
        for iid, (title, abstract) in docs.items():
            conn.execute(
                "INSERT INTO corpus_embeddings "
                "(item_id, title, abstract, tags_json, content_hash, embedding_json) "
                "VALUES (?, ?, ?, '[]', 'h', '[]')",
                (iid, title, abstract),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def test_bm25_ranks_lexical_match(tmp_path):
    db = _seed_corpus(tmp_path, {
        "A": ("Hallucination mitigation in LLMs", "reducing hallucinations"),
        "B": ("Graph neural networks", "molecular property prediction"),
        "C": ("Mitigation strategies", "broad mitigation overview"),
    })
    bm = corpus_bm25.CorpusBM25(db)
    out = bm.search("hallucination mitigation", ["A", "B", "C"], top_k=10)
    ranked = sorted(out, key=out.get, reverse=True)
    assert ranked and ranked[0] == "A"   # matches both query terms
    assert "B" not in out                # no query token → score 0, filtered


def test_bm25_empty_query(tmp_path):
    db = _seed_corpus(tmp_path, {"A": ("x", "y")})
    assert corpus_bm25.CorpusBM25(db).search("", ["A"], top_k=5) == {}


def test_bm25_restricts_to_candidates(tmp_path):
    # "mitigation" in a minority of docs → positive IDF (a real selective term).
    db = _seed_corpus(tmp_path, {
        "A": ("mitigation method", ""), "B": ("mitigation method", ""),
        "C": ("unrelated", ""), "D": ("other topic", ""), "E": ("more text", ""),
    })
    out = corpus_bm25.CorpusBM25(db).search("mitigation", ["A"], top_k=5)
    assert set(out) == {"A"}             # B excluded — not a candidate


def test_bm25_cache_rebuilds_on_corpus_change(tmp_path):
    db = _seed_corpus(tmp_path, {"A": ("alpha", ""), "B": ("filler one", ""), "C": ("filler two", "")})
    bm = corpus_bm25.CorpusBM25(db)
    assert "A" in bm.search("alpha", ["A"])
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO corpus_embeddings (item_id, title, abstract, tags_json, content_hash, embedding_json) "
        "VALUES ('Z', 'beta', '', '[]', 'h', '[]')"
    )
    conn.commit()
    conn.close()
    assert "Z" in bm.search("beta", ["Z"])   # row count changed → index rebuilt


def test_texts_for(tmp_path):
    db = _seed_corpus(tmp_path, {"A": ("Title A", "Abstract A")})
    assert corpus_bm25.CorpusBM25(db).texts_for(["A"])["A"] == "Title A. Abstract A"
