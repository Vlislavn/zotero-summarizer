"""Corpus affinity must not contain the candidate's own engagement signal."""

from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.models import CorpusItem, SummarizeRequest
from zotero_summarizer.services import corpus as service
from zotero_summarizer.services.model.classifier_features import _compute_aux_with_context
from zotero_summarizer.storage import migrations
from zotero_summarizer.storage.corpus import EmbeddingCache


def _score(cache, title, doi, route, monkeypatch):
    if route == "model":
        affinity, _, ctx = _compute_aux_with_context(
            cache, None, title=title, abstract="Abstract", doi=doi, year=None,
        )
        return affinity, ctx["goal_sims"]["goal"]
    if route == "service":
        runtime = SimpleNamespace(
            embedding_cache=cache,
            app_state=SimpleNamespace(config=SimpleNamespace(
                corpus=SimpleNamespace(enabled=True, stale_days_for_weak_negative=30),
            )),
        )
        monkeypatch.setattr(service, "state", lambda: runtime)
        result = service.run_corpus_match(
            SummarizeRequest(title=title, abstract="Abstract", doi=doi), "",
        )
        return result["affinity_score"], result["matched_goal_similarity"]
    kwargs = {"doi": doi} if doi else {}
    if route == "fast":
        affinity, goals = cache.affinity_and_goals(title, "Abstract", **kwargs)
        return affinity, goals["goal"]
    result = cache.match_candidate(title, "Abstract", **kwargs)
    if result.affinity_score == 0:
        assert result.top_similar_items == []
        assert result.suggested_collections == ["Other"]
    return result.affinity_score, result.matched_goal_similarity


@pytest.mark.parametrize("route", ["fast", "full", "model", "service"])
@pytest.mark.parametrize("identity,tag,expected", [
    ("title", "🧠", 0), ("doi", "🧠", 0), ("legacy_title", "🧠", 0),
    ("different_doi", "🧠", 0.6667), ("doi", "❌", 0),
])
def test_own_paper_excluded_but_other_evidence_and_goals_retained(
    tmp_path, monkeypatch, route, identity, tag, expected,
):
    cache = EmbeddingCache(tmp_path / "corpus.db", "test-model")
    monkeypatch.setattr(cache, "_embed", lambda text: (
        [0.0, 1.0] if text.startswith(("Other", "Negative")) else [1.0, 0.0]
    ))
    stored_doi = "10.1234/paper" if identity in {"doi", "different_doi"} else ""
    candidate_doi = "" if identity == "title" else (
        "10.1234/unrelated" if identity == "different_doi" else "https://doi.org/10.1234/PAPER"
    )
    title = "Renamed article" if identity == "doi" else " paper  title "
    cache.upsert_items([
        CorpusItem(item_id="A", title="Paper Title", doi=stored_doi, tags=[tag], collections=["Self"]),
        CorpusItem(item_id="B", title="Earlier version" if identity == "doi" else "PAPER TITLE",
                   doi=stored_doi, tags=[tag], collections=["Self"]),
        CorpusItem(item_id="C", title="Other paper", tags=["🧠"], collections=["Other"]),
        CorpusItem(item_id="D", title="Negative paper", tags=["❌"]),
    ])
    cache.upsert_goals(["goal"])

    assert _score(cache, title, candidate_doi, route, monkeypatch) == (expected, 1.0)
    # Exclusion is request-local, including when the matrix cache is warm.
    assert _score(cache, "Unrelated new article", "", route, monkeypatch) == (
        -0.6667 if tag == "❌" else 0.6667, 1.0,
    )
    assert len(cache.list_item_ids()) == 4


def test_doi_migration_and_metadata_refresh_preserve_vectors(tmp_path, monkeypatch):
    path = tmp_path / "corpus.db"
    with monkeypatch.context() as old_schema:
        old_schema.setattr(migrations, "CORPUS_MIGRATIONS", [
            m for m in migrations.CORPUS_MIGRATIONS if m.version <= 5
        ])
        old = EmbeddingCache(path, "test-model")
    with closing(old._conn()) as conn:
        conn.execute(
            "INSERT INTO corpus_embeddings (item_id,title,abstract,content_hash,embedding_json,encoder_id) "
            "VALUES ('A','Paper','Abstract',?,'[1.0, 0.0]',?)",
            (old._content_hash("Paper", "Abstract"), old._encoder_id),
        )
        conn.commit()
    cache = EmbeddingCache(path, "test-model")
    assert cache.get_item_metadata("A")["doi"] == ""
    embed = Mock(side_effect=AssertionError("unchanged text must not be re-embedded"))
    monkeypatch.setattr(cache, "_embed", embed)
    item = service._corpus_item_from_zotero_detail({
        "item_key": "A", "title": "Paper", "abstract": "Abstract", "doi": "10.1234/paper",
    })

    assert cache.upsert_items([item]) == (0, 1)
    assert cache.upsert_items([item]) == (0, 0)
    assert cache.get_item_metadata("A")["doi"] == "10.1234/paper"
    assert cache.list_items_metadata()["items"][0]["doi"] == "10.1234/paper"
    with closing(cache._conn()) as conn:
        assert conn.execute("SELECT embedding_json FROM corpus_embeddings").fetchone()[0] == "[1.0, 0.0]"
    assert EmbeddingCache(path, "test-model").get_item_metadata("A")["doi"] == "10.1234/paper"
    embed.assert_not_called()


def test_warm_identity_cache_observes_external_doi_update(tmp_path, monkeypatch):
    from zotero_summarizer.services.model.classifier_inputs import _corpus_rows

    path = tmp_path / "corpus.db"
    cache = EmbeddingCache(path, "test-model")
    monkeypatch.setattr(cache, "_embed", lambda text: [1.0, 0.0])
    item = CorpusItem(item_id="A", title="Same title", tags=["🧠"])
    cache.upsert_items([item])
    assert cache.affinity_and_goals("Same title", "", doi="10.1234/query")[0] == 0
    before = _corpus_rows(path)
    writer = EmbeddingCache(path, "test-model")
    monkeypatch.setattr(writer, "_embed", Mock(side_effect=AssertionError("metadata-only write")))

    assert writer.upsert_items([item.model_copy(update={"doi": "10.1234/distinct"})]) == (0, 1)

    assert _corpus_rows(path) != before
    assert cache.affinity_and_goals("Same title", "", doi="10.1234/query")[0] == 1
    assert cache.match_candidate("Same title", "", doi="10.1234/query").affinity_score == 1
