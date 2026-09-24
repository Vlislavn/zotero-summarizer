"""Corpus vectors cannot cross encoder identities or conceal load failures."""
from unittest.mock import Mock

import pytest

from zotero_summarizer.models import CorpusItem
from zotero_summarizer.storage import corpus


def test_model_change_reembeds_unchanged_content(tmp_path, monkeypatch):
    path = tmp_path / "corpus.db"
    item = CorpusItem(item_id="A", title="Same paper", abstract="Same abstract")
    old = corpus.EmbeddingCache(path, "old-model")
    monkeypatch.setattr(old, "_embed", lambda text: [1.0, 0.0])
    old.upsert_items([item])
    new = corpus.EmbeddingCache(path, "new-model")
    embed = Mock(return_value=[0.0, 0.0, 1.0])
    monkeypatch.setattr(new, "_embed", embed)

    assert new.upsert_items([item]) == (0, 1)
    embed.assert_called_once()
    assert new.query_affinity_for_items("query", ["A"]) == {"A": 1.0}
    assert new.upsert_items([item]) == (0, 0)


@pytest.mark.parametrize("reader", ["fast", "full", "query", "goals"])
def test_readers_reject_other_model_even_with_warm_matrix(tmp_path, monkeypatch, reader):
    path = tmp_path / "corpus.db"
    old = corpus.EmbeddingCache(path, "old-model")
    monkeypatch.setattr(old, "_embed", lambda text: [1.0, 0.0])
    old.upsert_items([CorpusItem(item_id="A", title="Paper", tags=["🧠"])])
    old.upsert_goals(["Goal"])
    old._normalized_corpus_matrix()
    old.affinity_and_goals("Candidate", "Abstract")
    new = corpus.EmbeddingCache(path, "new-model")
    monkeypatch.setattr(new, "_embed", lambda text: [1.0, 0.0])
    calls = {
        "fast": lambda: new.affinity_and_goals("Candidate", "Abstract"),
        "full": lambda: new.match_candidate("Candidate", "Abstract"),
        "query": lambda: new.query_affinity_for_items("query", ["A"]),
        "goals": lambda: new.goal_affinity_for_items(["A"]),
    }
    with pytest.raises(ValueError, match="encoder.*resync"):
        calls[reader]()


def test_failed_load_writes_no_vectors_and_can_retry(tmp_path, monkeypatch):
    factory = Mock(side_effect=RuntimeError("encoder unavailable"))
    monkeypatch.setattr(corpus, "SentenceTransformer", factory)
    cache = corpus.EmbeddingCache(tmp_path / "corpus.db", "model")
    item = CorpusItem(item_id="A", title="Paper")
    with pytest.raises(RuntimeError, match="encoder unavailable"):
        cache.upsert_items([item])
    with cache._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM corpus_embeddings").fetchone()[0] == 0
    model = Mock()
    model.encode.return_value = [1.0, 0.0]
    factory.side_effect = None
    factory.return_value = model
    assert cache.upsert_items([item]) == (1, 0)
    assert factory.call_count == 2


def test_legacy_migration_preserves_metadata_but_requires_reembedding(tmp_path, monkeypatch):
    from zotero_summarizer.storage import migrations

    path = tmp_path / "legacy.db"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "CORPUS_MIGRATIONS", migrations.CORPUS_MIGRATIONS[:3])
        legacy = corpus.EmbeddingCache(path, "model")
    with legacy._conn() as conn:
        conn.execute("INSERT INTO corpus_embeddings (item_id, title, content_hash, embedding_json) "
                     "VALUES ('A', 'Paper', 'old-hash', '[1, 0]')")
        conn.execute("INSERT INTO goal_embeddings (goal, embedding_json) VALUES ('Goal', '[1, 0]')")
    cache = corpus.EmbeddingCache(path, "model")
    assert cache.get_item_metadata("A")["title"] == "Paper"
    with cache._conn() as conn:
        assert conn.execute("SELECT encoder_id FROM corpus_embeddings").fetchone()[0] is None
        assert conn.execute("SELECT encoder_id FROM goal_embeddings").fetchone()[0] is None
        assert conn.execute("SELECT version FROM schema_migrations WHERE namespace='corpus'").fetchone()[0] == migrations.SCHEMA_VERSION
    with pytest.raises(ValueError, match="encoder.*resync"):
        cache.goal_affinity_for_items(["A"])
    cache.upsert_items([CorpusItem(item_id="A", title="Paper")])
    with pytest.raises(ValueError, match="encoder.*resync"):
        cache.goal_affinity_for_items(["A"])
    cache.upsert_goals(["Goal"])
    assert cache.goal_affinity_for_items(["A"]) == {"A": 1.0}
    assert corpus.EmbeddingCache(path, "model").goal_affinity_for_items(["A"]) == {"A": 1.0}


def test_other_instance_reembedding_invalidates_warm_affinity(tmp_path, monkeypatch):
    path = tmp_path / "corpus.db"
    old = corpus.EmbeddingCache(path, "old-model")
    item = CorpusItem(item_id="A", title="Paper", tags=["🧠"])
    old.upsert_items([item])
    assert old.affinity_and_goals("Candidate", "Abstract")[0] == 1.0
    new = corpus.EmbeddingCache(path, "new-model")
    monkeypatch.setattr(new, "_embed", lambda text: [1.0, 0.0])
    new.upsert_items([item])
    with pytest.raises(ValueError, match="encoder.*resync"):
        old.affinity_and_goals("Candidate", "Abstract")
    assert new.affinity_and_goals("Candidate", "Abstract")[0] == 1.0


@pytest.mark.parametrize("table", ["corpus_embeddings", "goal_embeddings"])
def test_failed_batch_keeps_previous_vectors(tmp_path, monkeypatch, table):
    cache = corpus.EmbeddingCache(tmp_path / "corpus.db", "model")
    cache.upsert_goals(["Old"])
    cache.upsert_items([CorpusItem(item_id="A", title="Old")])
    with cache._conn() as conn:
        before = [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
    monkeypatch.setattr(cache, "_embed", Mock(side_effect=[[0.0, 1.0], RuntimeError("encode failed")]))
    with pytest.raises(RuntimeError, match="encode failed"):
        if table == "corpus_embeddings":
            cache.upsert_items([CorpusItem(item_id="A", title="New"), CorpusItem(item_id="B", title="New")])
        else:
            cache.upsert_goals(["First", "Second"])
    with cache._conn() as conn:
        assert [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] == before


@pytest.mark.parametrize("raw", ["broken", "{}", "[]", '["1"]', "[NaN]", "[Infinity]"])
def test_corrupt_vectors_raise(raw):
    with pytest.raises(ValueError):
        corpus.EmbeddingCache._parse_embedding(raw)


def test_incompatible_dimensions_are_not_truncated():
    with pytest.raises(ValueError, match="dimensions differ"):
        corpus.EmbeddingCache._cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_classifier_does_not_hide_corpus_errors(tmp_path, monkeypatch):
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services.model import classifier_features as features

    monkeypatch.setattr(features, "_resolve_embedding_cache", Mock(side_effect=ValueError("bad corpus")))
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "test", "refine_model": "test",
        "api_base": "http://localhost", "api_key_env": "TEST_KEY",
    })
    with pytest.raises(ValueError, match="bad corpus"):
        features._build_aux_providers(tmp_path / "corpus.db", config)
    cache = Mock()
    cache.affinity_and_goals.side_effect = ValueError("bad corpus")
    with pytest.raises(ValueError, match="bad corpus"):
        features._compute_aux(cache, None, title="Paper", abstract="Abstract", doi="", year=None)


def test_migration_ddl_and_version_are_atomic(tmp_path):
    from zotero_summarizer.storage.migrations import Migration, run_migrations
    import sqlite3

    path = tmp_path / "migration.db"
    def fail(conn):
        conn.execute("CREATE TABLE incomplete (id INTEGER)")
        raise RuntimeError("migration interrupted")
    with pytest.raises(RuntimeError, match="migration interrupted"):
        run_migrations(path, "test", [Migration(1, "failing", fail)])
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='incomplete'").fetchone() is None
        assert conn.execute("SELECT version FROM schema_migrations WHERE namespace='test'").fetchone() is None


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_optional_wal_absence_is_not_a_permission_fallback(tmp_path, monkeypatch, error):
    from zotero_summarizer.storage import corpus_read

    path = tmp_path / "corpus.db"
    corpus.EmbeddingCache(path, "model")
    stat = corpus_read.os.stat
    def wal_stat(target, *args, **kwargs):
        if str(target).endswith("-wal"):
            raise error("WAL unavailable")
        return stat(target, *args, **kwargs)
    monkeypatch.setattr(corpus_read.os, "stat", wal_stat)
    if error is FileNotFoundError:
        assert corpus_read._corpus_fingerprint(str(path))[-2:] == (0, 0)
    else:
        with pytest.raises(PermissionError):
            corpus_read._corpus_fingerprint(str(path))
