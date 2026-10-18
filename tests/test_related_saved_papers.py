"""Machine/assembly contracts only: scripted judgments do not measure usefulness."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import library
from zotero_summarizer.models.providers import LLMRoutingConfig
from zotero_summarizer.services.library import related, _related_assessment as semantic, _related_context as context, _search
from zotero_summarizer.services.search import session
from zotero_summarizer.services.zotero._notes import build_user_note_html, build_verdict_note_html
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.library_context import read_context
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations

STABLE = "feed:g:" + "a" * 64


class Reader:
    def __init__(self):
        self.rows = {key: {"item_key": key, "title": title, "abstract": abstract,
                           "notes": [], "annotations": [], "doi": doi}
                     for key, title, abstract, doi in [
                         ("SEED0001", "Seed paper", "Seed metadata about reliable evaluation.", "10.1/seed"),
                         ("GOOD0001", "Useful comparison", "Candidate metadata about reliable evaluation.", "10.1/good"),
                         ("BAD00001", "Rejected method", "Negative candidate metadata.", "10.1/bad"),
                         ("UNKNOWN1", "Uncertain method", "Uncertain candidate metadata.", "10.1/unknown"),
                         ("HIDDEN01", "Human rejected", "Do not recommend this saved item.", "10.1/hidden"),
                     ]}
        self.scopes = []
        self.fail = None

    def get_all_items(self, **scope):
        self.scopes.append(scope)
        return {"items": list(self.rows.values())}

    def get_item_detail(self, key):
        if key == self.fail:
            raise OSError("injected unavailable native source")
        return deepcopy(self.rows.get("SEED0001" if key == STABLE else key))


class Model:
    def __init__(self):
        self.payloads = []
        self.prompts = []
        self.mutate = None

    def pydantic_prompt(self, *, prompt, pydantic_model):
        from html import unescape

        assert pydantic_model is semantic.Assessments
        payload = json.loads(unescape(prompt.rsplit("<untrusted_input>", 1)[1].split("</untrusted_input>", 1)[0]))
        self.payloads.append(payload)
        self.prompts.append(prompt)
        rows = []
        for paper in payload["candidates"]:
            key = paper["item_key"]
            rows.append({"item_key": key, "relevance": "unknown" if key == "UNKNOWN1" else "relevant",
                         "preference": "conflict" if key == "BAD00001" else "unknown" if key == "UNKNOWN1" else "compatible",
                         "sentence": "Both metadata records discuss reliable evaluation; model judgment only.",
                         "seed_quote": {"field": "abstract", "excerpt": payload["seed"]["abstract"]},
                         "candidate_quote": {"field": "abstract", "excerpt": paper["abstract"]},
                         "used_context_ids": [record["id"] for p in (payload["seed"], paper) for record in p["comments"]["records"]]})
        if self.mutate:
            self.mutate(rows)
        return pydantic_model.model_validate({"items": rows})


@pytest.fixture
def journey(tmp_path, monkeypatch):
    db = tmp_path / "triage.db"
    run_migrations(db, "triage", TRIAGE_MIGRATIONS)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                     "materialized_zotero_key, title, guid, decision, run_id) "
                     "VALUES (1, 42, ?, 'SEED0001', 'Seed paper', 'guid', 'selected', 'test')", (STABLE,))
    repositories.upsert_review_note(db, "feed:42", "Не нужны данные без контроля. Keep negation.")
    repositories.upsert_review_note(db, "GOOD0001", "Short RU/EN: полезно для сравнения.")
    repositories.insert_or_update_label_verdict(db, item_key="HIDDEN01", original_derived_priority="should_read",
                                               user_priority="dont_read", comment="Explicit human exclusion")
    routing = LLMRoutingConfig.model_validate({
        "providers": [{"name": "test-provider", "base_url": "http://127.0.0.1:9999/v1", "api_key_env": "TEST_KEY"}],
        "default": {"provider": "test-provider", "model": "test-model"},
    })
    cfg = SimpleNamespace(llm_enabled=True, llm_routing=routing,
                          corpus=SimpleNamespace(enabled=True, bm25_enabled=True, reranker_enabled=False))
    cfg.model_dump = lambda **kwargs: {"routing": routing.model_dump(mode="json"), "llm_enabled": cfg.llm_enabled}
    model, reader = Model(), Reader()
    app = SimpleNamespace(app_state=SimpleNamespace(config=cfg), embedding_cache=None,
                          resolve_stage_client=lambda stage, expected_identity=None: model)
    paths = SimpleNamespace(triage_db_path=db, corpus_db_path=tmp_path / "corpus.db",
                            search_dir=tmp_path / "search", summary_timeout_seconds=30)
    monkeypatch.setattr(related, "settings", lambda: paths)
    monkeypatch.setattr(session, "settings", lambda: paths)
    monkeypatch.setattr(related, "state", lambda: app)
    monkeypatch.setattr(semantic, "state", lambda: app)
    monkeypatch.setattr(semantic, "offline_requested", lambda: False)
    monkeypatch.setattr(related, "get_library_reader", lambda: reader)
    monkeypatch.setattr(related, "resolve_reader_for_key", lambda key: reader)
    monkeypatch.setattr(_search, "get_state", lambda: app)
    monkeypatch.setattr(_search, "get_settings", lambda: paths)
    queries = []

    def bm_search(query, keys, **kwargs):
        queries.append(query)
        return {key: 1 / (i + 1) for i, key in enumerate(keys)}

    monkeypatch.setattr(_search, "get_corpus_bm25", lambda path: SimpleNamespace(search=bm_search))
    return SimpleNamespace(db=db, reader=reader, model=model, paths=paths, config=cfg, queries=queries)


def test_http_assembly_default_then_explicit_context_and_saved_receipt(journey):
    app = FastAPI()
    app.include_router(library.router)
    with TestClient(app) as client:
        baseline = client.get("/api/library/related/SEED0001").json()
        assert baseline["status"] == "ready"
        assert baseline["comments_in_ranking"] is False
        assert journey.model.payloads == []
        assert baseline["excluded_human_dont_read"] == ["HIDDEN01"]
        response = client.post("/api/library/related/SEED0001/assess", json={"consent_token": baseline["boundary"]["token"]})
        assert response.status_code == 200
        result = response.json()
        assert result["assessment_status"] == "assessed"
        assert [row["item_key"] for row in result["items"]] == ["GOOD0001"]
        assert result["comments_in_ranking"] is True
        assert len(result["items"][0]["evidence"]) == 2
        assert result["items"][0]["evidence"]["seed"]["item_key"] == "SEED0001"
        again = client.post("/api/library/related/SEED0001/assess", json={"consent_token": baseline["boundary"]["token"]})
        assert again.json() == result
    payload = journey.model.payloads[0]
    assert payload["seed"]["comments"]["records"][0]["item_key"] == "feed:42"
    assert "Не нужны данные без контроля" in payload["seed"]["comments"]["records"][0]["text"]
    assert "полезно" in payload["candidates"][0]["comments"]["records"][0]["text"]
    assert all("Не нужны" not in query and "полезно" not in query for query in journey.queries)
    assert len(journey.model.payloads) == 1
    persisted = session.load(result["session_id"])
    assert persisted.status == "related_complete"
    assert persisted.related["result"] == result
    assert session.list_sessions() == []  # no Search auto-review surface


def test_both_review_keys_exclude_self_alias_and_preserve_filters(journey):
    for key in (STABLE, "SEED0001"):
        result = related.related_papers(key, collection="COLLECT1", tag="👀 read")
        assert "SEED0001" not in [row["item_key"] for row in result["items"]]
        assert journey.reader.scopes[-1] == {"collection_key": "COLLECT1", "tag": "👀 read", "include_abstract": True}
        assert result["seed_comments"]["records"][0]["item_key"] == "feed:42"


@pytest.mark.parametrize("change", ["seed", "candidate", "delete", "native", "model", "corpus"])
def test_same_count_edits_deletions_and_source_identity_invalidate(journey, change):
    token = semantic.model_boundary()["token"]
    first = related.assess_related("SEED0001", consent_token=token)
    if change == "seed":
        repositories.upsert_review_note(journey.db, "SEED0001", "Different same-count note")
    elif change == "candidate":
        repositories.upsert_review_note(journey.db, "GOOD0001", "Different candidate text")
    elif change == "delete":
        with sqlite3.connect(journey.db) as conn:
            conn.execute("DELETE FROM review_notes WHERE item_key='GOOD0001'")
    elif change == "native":
        journey.reader.rows["GOOD0001"]["notes"] = [{"note_key": "NOTE0001", "note": "Changed", "date_modified": "same-clock"}]
    elif change == "model":
        journey.config.llm_routing.default.model = "different-model"
    else:
        journey.paths.corpus_db_path.write_bytes(b"changed public fixture")
    second = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert first["context_fingerprint"] != second["context_fingerprint"]
    assert first["session_id"] != second["session_id"]
    assert len(journey.model.payloads) == 2
    assert session.load(first["session_id"]).related["result"] == first


def test_concurrent_clicks_share_one_claim_and_terminal_result(journey):
    token = semantic.model_boundary()["token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: related.assess_related("SEED0001", consent_token=token), range(2)))
    assert results[0] == results[1]
    assert len(journey.model.payloads) == 1


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows.pop(),
    lambda rows: rows[0].update(item_key="FOREIGN1"),
    lambda rows: rows[0].update(used_context_ids=["foreign-note"]),
    lambda rows: rows[0]["seed_quote"].update(excerpt="fabricated quote"),
    lambda rows: rows[0].update(candidate_quote=None),
    lambda rows: rows[0].update(relevance="maybe"),
])
def test_invalid_machine_contract_is_persisted_failure_not_empty_success(journey, mutation):
    journey.model.mutate = mutation
    token = semantic.model_boundary()["token"]
    result = related.assess_related("SEED0001", consent_token=token)
    assert result["assessment_status"] == "failed"
    assert result["comments_in_ranking"] is False
    assert session.load(result["session_id"]).status == "related_failed"
    assert related.assess_related("SEED0001", consent_token=token) == result
    assert len(journey.model.payloads) == 1
    journey.model.mutate = None
    recovered = related.assess_related("SEED0001", consent_token=token, retry=True)
    assert recovered["assessment_status"] == "assessed"
    assert session.load(result["session_id"]).related["prior_attempts"][0]["result"] == result


def test_budget_does_not_clip_and_old_consent_never_calls_model(journey, monkeypatch):
    baseline = related.related_papers("SEED0001")
    journey.config.llm_routing.default.model = "new-model"
    with pytest.raises(APIError):
        related.assess_related("SEED0001", consent_token=baseline["boundary"]["token"])
    monkeypatch.setenv("ZS_RELATED_INPUT_CHARS", "1000")
    result = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert result["assessment_status"] == "budget_exceeded"
    assert result["comments_in_ranking"] is False
    assert journey.model.payloads == []
    monkeypatch.setenv("ZS_RELATED_CANDIDATES", "11")
    with pytest.raises(ValueError):
        semantic.budgets()


def test_empty_unknown_failed_native_and_missing_comment_paths_stay_distinct(journey):
    for key in ("feed:42", "GOOD0001"):
        repositories.upsert_review_note(journey.db, key, "")
    empty = related.related_papers("SEED0001")
    assert empty["status"] == "ready"
    assert empty["seed_comments"]["availability"] == "empty"
    no_comments = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert no_comments["assessment_status"] == "no_comments"
    assert journey.model.payloads == []
    journey.reader.fail = "GOOD0001"
    partial = related.related_papers("SEED0001")
    assert partial["items"][0]["comments"]["native_state"] == "failed"
    unavailable = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert unavailable["assessment_status"] == "context_unavailable"
    assert journey.model.payloads == []
    with sqlite3.connect(journey.db) as conn:
        conn.execute("DROP TABLE review_notes")
    with pytest.raises(sqlite3.OperationalError):
        related.related_papers("SEED0001")  # schema failure is not observed empty


def test_alias_tombstone_excludes_mirrors_generated_notes_and_preserves_native_text(journey):
    with sqlite3.connect(journey.db) as conn:
        conn.execute("DELETE FROM review_notes WHERE item_key='feed:42'")
    snapshot = read_context(journey.db, ["SEED0001", STABLE])
    assert snapshot[STABLE]["manual"]["value"] is None
    assert snapshot[STABLE]["manual"]["revision"] > 0
    detail = {"notes": [
        {"note_key": "MIRROR01", "note": build_user_note_html("deleted old mirror")},
        {"note_key": "VERDICT1", "note": build_verdict_note_html("must_read", "mirrored verdict")},
        {"note_key": "DIGEST01", "note": "<!-- zs:note_type=digest;version=1 --><h2>Generated</h2>"},
        {"note_key": "MANUAL01", "note": "<p>Нет. Not useful. zs:note_type=digest is body text.</p>"},
    ], "annotations": [{"annotation_key": "ANNO0001", "comment": "Нет", "text": "Paper highlight is separate"}]}
    comments = context.collect_comments("SEED0001", detail, snapshot["SEED0001"], "observed", "observed")
    assert len(comments["excluded_owned_notes"]) == 3
    assert [record["source"] for record in comments["records"]] == ["native_note", "annotation_comment"]
    assert comments["records"][0]["text"].startswith("Нет. Not useful.")
    assert comments["records"][1]["paper_excerpt"] == "Paper highlight is separate"
    assert comments["records"][1]["text"] == "Нет"


def test_inflight_source_change_never_publishes_current_assessment(journey):
    journey.model.mutate = lambda rows: repositories.upsert_review_note(journey.db, "GOOD0001", "Edited while assessing")
    result = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert result["assessment_status"] == "stale"
    assert result["comments_in_ranking"] is False


@pytest.mark.parametrize("phase", ["related_pending", "related_running"])
def test_interrupted_attempt_requires_explicit_retry_and_retains_claim(journey, phase):
    token = semantic.model_boundary()["token"]
    complete = related.assess_related("SEED0001", consent_token=token)
    sess = session.load(complete["session_id"])
    sess.status, sess.related["result"] = phase, None
    session.save(sess)
    interrupted = related.assess_related("SEED0001", consent_token=token)
    assert interrupted["assessment_status"] == "interrupted"
    assert len(journey.model.payloads) == 1
    recovered = related.assess_related("SEED0001", consent_token=token, retry=True)
    assert recovered["assessment_status"] == "assessed"
    assert session.load(sess.id).related["prior_attempts"][0]["status"] == phase


def test_unknown_preference_does_not_promote_or_mutate_user_tables(journey):
    def unknown(rows):
        rows[0]["preference"] = "unknown"
    journey.model.mutate = unknown
    before = read_context(journey.db, ["SEED0001", "GOOD0001"])
    result = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert result["status"] == "empty"
    assert result["assessment_status"] == "assessed"
    assert read_context(journey.db, ["SEED0001", "GOOD0001"]) == before
    assert not any(path.name == "deep_reviews.json" for path in journey.paths.search_dir.iterdir())


def test_native_unknown_app_unavailable_and_unexpected_failures_are_not_empty(journey, monkeypatch):
    detail, availability = context.native_detail(journey.reader, "MISSING1")
    assert detail is None and availability == "unknown"
    missing, status = context.app_context(journey.paths.search_dir / "missing.db", ["SEED0001"])
    assert missing == {} and status == "failed"
    comments = context.collect_comments("SEED0001", None, None, "failed", "unavailable")
    assert comments["availability"] == "unavailable"
    assert comments["records"] == []

    def broken(key):
        raise ValueError("unexpected programming failure")
    monkeypatch.setattr(journey.reader, "get_item_detail", broken)
    with pytest.raises(ValueError):
        context.native_detail(journey.reader, "SEED0001")


def test_prompt_revision_and_scope_participate_in_context_identity(journey, monkeypatch):
    first = related.related_papers("SEED0001")
    monkeypatch.setattr(semantic, "_PROMPT", semantic._PROMPT + "\nChanged policy version.")
    second = related.related_papers("SEED0001")
    scoped = related.related_papers("SEED0001", tag="read")
    assert len({first["context_fingerprint"], second["context_fingerprint"], scoped["context_fingerprint"]}) == 3


def test_today_seed_reads_native_comments_from_its_materialized_alias(journey, monkeypatch):
    from zotero_summarizer.services.library.app_library_reader import AppLibraryReader

    app_reader = AppLibraryReader(journey.db)
    monkeypatch.setattr(app_reader, "get_item_detail", lambda key: {
        **journey.reader.rows["SEED0001"], "item_key": key, "notes": [], "annotations": [],
    })
    monkeypatch.setattr(related, "resolve_reader_for_key", lambda key: app_reader)
    journey.reader.rows["SEED0001"]["notes"] = [{"note_key": "NATIVE01", "note": "<p>Не повторять.</p>"}]
    result = related.related_papers(STABLE)
    records = result["seed_comments"]["records"]
    assert [record["item_key"] for record in records] == ["feed:42", "SEED0001"]
    assert result["seed_comments"]["native_sources"]["SEED0001"] == "observed"
    assert records[1]["text"] == "Не повторять."
    assert all(row["item_key"] != "SEED0001" for row in result["items"])


def test_machine_negative_label_never_becomes_authoritative_human_exclusion(journey):
    repositories.insert_or_update_label_verdict(journey.db, item_key="HIDDEN01", original_derived_priority="should_read",
                                               user_priority="dont_read", comment="Machine suggestion", source="machine_add")
    result = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert result["excluded_human_dont_read"] == []
    machine = next(paper for paper in journey.model.payloads[0]["candidates"] if paper["item_key"] == "HIDDEN01")
    assert machine["human_verdict"] is None
    assert machine["comments"]["records"] == []


def test_submitted_context_is_not_misreported_as_model_cited_use(journey):
    def no_citations(rows):
        for row in rows:
            row["used_context_ids"] = []
    journey.model.mutate = no_citations
    result = related.assess_related("SEED0001", consent_token=semantic.model_boundary()["token"])
    assert result["assessment_status"] == "assessed"
    assert result["submitted_context_ids"]
    assert result["comments_in_ranking"] is False


def test_bounded_snapshot_omits_unassessed_context_but_retains_its_invalidation(journey, monkeypatch):
    monkeypatch.setenv("ZS_RELATED_CANDIDATES", "1")
    journey.reader.rows["OUTSIDE1"] = {"item_key": "OUTSIDE1", "title": "Outside assessed pool", "abstract": "Other metadata"}
    repositories.upsert_review_note(journey.db, "OUTSIDE1", "UNASSESSED PRIVATE CONTEXT")
    token = semantic.model_boundary()["token"]
    first = related.assess_related("SEED0001", consent_token=token)
    persisted = session.load(first["session_id"])
    snapshot = persisted.related["snapshot"]
    assert [paper["item_key"] for paper in snapshot["pool"]] == ["GOOD0001"]
    assert "items" not in snapshot and "contexts" not in snapshot
    assert len(snapshot["library_identity"]) == 64
    assert "UNASSESSED PRIVATE CONTEXT" not in json.dumps(persisted.related)
    repositories.upsert_review_note(journey.db, "OUTSIDE1", "CHANGED UNASSESSED PRIVATE CONTEXT")
    second = related.assess_related("SEED0001", consent_token=token)
    assert second["context_fingerprint"] != first["context_fingerprint"]
    assert len(journey.model.payloads) == 2
    assert session.load(first["session_id"]).related["result"] == first
