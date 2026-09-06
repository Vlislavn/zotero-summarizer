"""Targeted Search materialization boundary checks."""

from __future__ import annotations

from zotero_summarizer.services.search._models import (
    Candidate,
    QueryPlan,
    ResearchSession,
    SearchIntent,
)


def _candidate(title: str, **kwargs) -> Candidate:
    return Candidate(title=title, **kwargs)


def _store(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import session

    class _Settings:
        search_dir = tmp_path

    monkeypatch.setattr(session, "settings", lambda: _Settings())
    return session


def _session(session_id: str, candidate: Candidate) -> ResearchSession:
    return ResearchSession(
        id=session_id,
        created_at="t",
        raw_query="q",
        intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(),
        candidates=[candidate],
        status="screened",
    )


def test_feed_payload_adapter_shape():
    from zotero_summarizer.services.search.materialize import _feed_payload

    candidate = _candidate(
        "T",
        abstract="A",
        doi="10.1/x",
        authors=["Alice", "Bob"],
        year=2025,
        venue="Nature",
    )
    payload = _feed_payload(candidate)
    assert payload["authors"] == ["Alice", "Bob"]
    assert payload["publication_date"] == "2025"
    assert payload["publication_title"] == "Nature"
    assert "year" not in payload
    assert payload["item_type"] == "journalArticle"


def test_materialize_rejects_unknown_candidate(monkeypatch, tmp_path):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.search import materialize, session

    _store(monkeypatch, tmp_path)
    session.save(_session("m1", _candidate("P", doi="10.1/x")))

    try:
        materialize.materialize_candidate("m1", "does-not-exist", None)
        assert False, "expected APIError for unknown candidate"
    except APIError as exc:
        assert exc.status_code == 404


def test_materialize_is_idempotent(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import materialize, session

    _store(monkeypatch, tmp_path)
    candidate = _candidate("P", doi="10.1/x", materialized_zotero_key="ZEXIST")
    session.save(_session("m2", candidate))
    assert materialize.materialize_candidate("m2", candidate.candidate_id, None) == {
        "status": "already_added",
        "zotero_key": "ZEXIST",
    }


def test_materialize_reuses_coverage_hit(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import materialize, session

    _store(monkeypatch, tmp_path)
    candidate = _candidate(
        "P",
        doi="10.1/x",
        in_library=True,
        existing_zotero_key="ZOLD",
    )
    session.save(_session("m-existing", candidate))
    monkeypatch.setattr(
        materialize,
        "ZoteroWriter",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate write")
        ),
    )

    result = materialize.materialize_candidate(
        "m-existing",
        candidate.candidate_id,
        None,
    )

    assert result == {"status": "already_added", "zotero_key": "ZOLD"}
    assert session.load("m-existing").candidates[0].materialized_zotero_key == "ZOLD"


def test_materialize_rejects_unknown_collection_key(monkeypatch, tmp_path):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.search import materialize, session

    class _Reader:
        def __init__(self, *args, **kwargs):
            pass

        def collection_name_for_key(self, key):
            return None

    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(materialize, "ZoteroReader", _Reader)
    monkeypatch.setattr(
        materialize,
        "ZoteroWriter",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected write")
        ),
    )
    research = _session("m3", _candidate("P", doi="10.1/x"))
    session.save(research)

    try:
        materialize.materialize_candidate(
            "m3",
            research.candidates[0].candidate_id,
            "BOGUSKEY",
        )
        assert False, "expected APIError for unknown collection key"
    except APIError as exc:
        assert exc.status_code == 400
