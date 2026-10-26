"""Synthetic API/SQL contracts; authored for parent execution, not live usefulness."""
from copy import deepcopy
from dataclasses import replace
from html import unescape
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import library, search
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services.library import _related_assessment as semantic
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.llm.factory import resolve_stage_client
from zotero_summarizer.services.search import discovery, session
from zotero_summarizer.services.search._models import Candidate, ResearchSession
from zotero_summarizer.services.zotero._notes import build_user_note_html
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.library_context import read_discovery_membership
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations

STABLE = "feed:g:" + "a" * 64
PRIVATE = "Не нужны неконтролируемые данные. Do NOT recommend without controls. PRIVATE_SENTINEL"


@pytest.fixture
def journey(tmp_path, monkeypatch):
    monkeypatch.setenv("ZS_OFFLINE", "0")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    paths = Settings.load(project_root=tmp_path)
    paths.data_dir.mkdir()
    run_migrations(paths.triage_db_path, "triage", TRIAGE_MIGRATIONS)
    with sqlite3.connect(paths.triage_db_path) as conn:
        conn.execute("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                     "materialized_zotero_key, title, doi, guid, decision, run_id) "
                     "VALUES (1, 42, ?, 'SEED0001', 'Public seed', '10.1/seed', 'seed', 'user_approved', 'test')", (STABLE,))
    native_db = build_zotero_db(tmp_path / "native")
    add_library_item(native_db, item_key="SEED0001", title="Public seed", abstract="Public controlled evaluation", doi="10.1/seed")
    reader = ZoteroReader(native_db.parent)
    paths = replace(paths, zotero_data_dir=native_db.parent)
    cfg = GoalsConfig.model_validate({"relevance_scale": {n: f"Synthetic {n}" for n in range(1, 6)},
        "llm": {"api_base": "http://127.0.0.1:9999/v1", "api_key_env": "TEST_KEY",
                "draft_model": "synthetic", "refine_model": "synthetic"}, "llm_routing": {
        "providers": [{"name": "test", "base_url": "http://127.0.0.1:9999/v1", "api_key_env": "TEST_KEY"}],
        "default": {"provider": "test", "model": "synthetic"}}})
    cfg.llm_enabled = True
    repositories.upsert_review_note(paths.triage_db_path, "feed:42", PRIVATE)
    requests, payloads = [], []
    controls = SimpleNamespace(provider_hook=None, model_hook=None, provider_status="success_nonempty", fail_model=False,
                               candidates=None)

    def prompt(*, prompt, pydantic_model):
        if controls.fail_model:
            raise ValueError("synthetic model failure")
        payload = json.loads(unescape(prompt.rsplit("<untrusted_input>", 1)[1].split("</untrusted_input>", 1)[0]))
        payloads.append(payload)
        if controls.model_hook:
            controls.model_hook()
        return pydantic_model.model_validate({"items": [{
            "item_key": row["item_key"], "relevance": "relevant", "preference": "compatible",
            "sentence": "Both supplied metadata records describe controlled evaluation.",
            "seed_quote": {"field": "abstract" if payload["seed"]["abstract"] else "title",
                           "excerpt": payload["seed"]["abstract"] or payload["seed"]["title"]},
            "candidate_quote": {"field": "abstract" if row["abstract"] else "title", "excerpt": row["abstract"] or row["title"]},
            "used_context_ids": [record["id"] for record in payload["seed"]["comments"]["records"]],
        } for row in payload["candidates"]]})

    runtime = SimpleNamespace(app_state=SimpleNamespace(config=cfg), zotero_reader=reader, embedding_cache=None)
    monkeypatch.setattr(factory, "resolve_stage_client", lambda *a, **kw: SimpleNamespace(pydantic_prompt=prompt))

    def federate(plan, *, observe_outcomes=False, **kwargs):
        # Frozen bridge only: actual receipt adapters belong to the provider component.
        assert observe_outcomes is True
        assert not plan.arxiv and not plan.arxiv_variants and not plan.europepmc and not plan.europepmc_variants
        requests.append(deepcopy(plan.to_dict()))
        if controls.provider_hook:
            controls.provider_hook()
        status = controls.provider_status
        for source, channel, queries in (
                ("arxiv", "arxiv", plan.arxiv_variants or [plan.arxiv]),
                ("europepmc", "europepmc", plan.europepmc_variants or [plan.europepmc]),
                ("openalex", "openalex_lexical", (plan.openalex_lexical_variants or [plan.openalex_lexical])[:2]),
                ("openalex", "openalex_semantic", [plan.openalex_semantic])):
            for query in (query for query in queries if query):
                plan.retrieval_accounting.setdefault(source, {"variants": []})["variants"].append({
                    "source": source, "query": query, "allocation": 15, "channel": channel,
                    "outcome": {"status": status, "count": 0 if status == "success_empty" else 2 if status == "success_nonempty" else None,
                                "error_code": "rate_limited" if status == "failed" else None}})
        if controls.candidates is not None:
            return deepcopy(controls.candidates)
        return [] if status != "success_nonempty" else [
            Candidate(title="Public candidate", abstract="Candidate controlled evaluation", doi="10.1/new"),
            Candidate(title="Unknown identity", abstract="Unknown metadata", candidate_id="local:unknown")]

    monkeypatch.setattr(discovery, "settings", lambda: paths)
    monkeypatch.setattr(session, "settings", lambda: paths)
    monkeypatch.setattr(discovery, "state", lambda: runtime)
    monkeypatch.setattr(semantic, "state", lambda: runtime)
    monkeypatch.setattr(discovery, "resolve_reader_for_key", lambda key: AppLibraryReader(paths.triage_db_path)
                        if key.startswith("feed:") or runtime.zotero_reader is None else reader)
    monkeypatch.setattr(discovery, "federate", federate)
    monkeypatch.setattr(discovery, "_search_openalex_client", lambda *a: object())
    monkeypatch.setattr(discovery, "score_query_relevance", lambda q, rows, **kw: [setattr(c, "query_score", 1.0) for c in rows])
    app = FastAPI()
    app.include_router(library.router)
    app.include_router(search.router)

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError):
        return JSONResponse({"error": exc.error}, status_code=exc.status_code)

    return SimpleNamespace(paths=paths, reader=reader, native_db=native_db, config=cfg, runtime=runtime,
                           controls=controls, requests=requests, payloads=payloads, client=TestClient(app))


def begin(journey, *, private=False, retry=False):
    metadata = journey.client.get("/api/library/related/SEED0001/discover").json()
    return journey.client.post("/api/library/related/SEED0001/discover", json={
        "context_fingerprint": metadata["context_fingerprint"], "retry": retry,
        "consent_token": metadata["boundary"]["token"] if private else None})


def test_api_public_find_get_then_opt_in_reuses_public_pool(journey, monkeypatch):
    monkeypatch.setattr(search, "_kickoff_review", lambda *a: pytest.fail("Find/GET started Review"))
    monkeypatch.setattr("zotero_summarizer.integrations.zotero_write.ZoteroWriter.apply_feed_materialization",
                        lambda *a, **kw: pytest.fail("Find/GET started native Add"))
    with sqlite3.connect(journey.native_db) as conn:
        native_before = list(conn.iterdump())
    metadata = journey.client.get("/api/library/related/SEED0001/discover").json()
    assert metadata["admission"] == {"state": "saved", "basis": "user_approved"}
    assert journey.requests == journey.payloads == []
    assert not journey.paths.search_dir.exists()

    result = begin(journey).json()
    assert result["status"] == "discovery_ready"
    assert result["discovery"]["assessment"]["status"] == "not_requested"
    assert result["discovery"]["unresolved_membership"] == ["local:unknown"]
    assert journey.payloads == []
    private = begin(journey, private=True).json()
    assert private["id"] == result["id"]
    assert len(journey.requests) == len(journey.payloads) == 1
    assert private["discovery"]["assessment"]["status"] == "assessed"
    assert private["discovery"]["recommended_ids"] == ["10.1/new"]
    assert "snapshot" not in private["discovery"]
    assert PRIVATE not in json.dumps(journey.requests, ensure_ascii=False)
    assert PRIVATE not in json.dumps({k: private[k] for k in ("raw_query", "intent", "plan", "candidates")}, ensure_ascii=False)
    seed = journey.payloads[0]["seed"]
    assert seed["comments"]["records"][0]["text"] == PRIVATE
    assert all(row["comments"]["availability"] == "unavailable" for row in journey.payloads[0]["candidates"])
    assert journey.client.get(f"/api/search/{result['id']}").json()["discovery"]["freshness"] == "current"
    with sqlite3.connect(journey.native_db) as conn:
        assert list(conn.iterdump()) == native_before


@pytest.mark.parametrize("body", [{}, {"context_fingerprint": "z" * 64},
    {"context_fingerprint": "a" * 64, "retry": 1},
    {"context_fingerprint": "a" * 64, "query": "private"},
    {"context_fingerprint": "a" * 64, "consent_token": "x" * 64}])
def test_strict_post_rejects_before_work(journey, body):
    response = journey.client.post("/api/library/related/SEED0001/discover", json=body)
    assert response.status_code == 422
    assert journey.requests == journey.payloads == []


def test_metadata_constructs_no_clients_and_native_failure_is_unverified(journey, monkeypatch):
    monkeypatch.setattr(factory, "resolve_stage_client", lambda *a, **kw: pytest.fail("model construction on GET"))
    monkeypatch.setattr(discovery, "_search_openalex_client", lambda *a: pytest.fail("provider construction on GET"))
    metadata = discovery.discovery_metadata("SEED0001")
    assert metadata["context_fingerprint"]
    monkeypatch.setattr(journey.reader, "get_field_values", lambda *a: (_ for _ in ()).throw(OSError("unavailable")))
    unavailable = discovery.discovery_metadata("SEED0001")
    assert unavailable["admission"]["state"] == "unknown"
    assert unavailable["context_fingerprint"] is None
    assert not journey.paths.search_dir.exists()


@pytest.mark.parametrize("decision", ["selected", "black_swan", "user_rejected"])
def test_selected_or_stored_materialized_key_is_not_seed_authority(journey, decision):
    journey.runtime.zotero_reader = None
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET decision=?", (decision,))
    metadata = discovery.discovery_metadata(STABLE)
    assert metadata["admission"] == {"state": "not_saved", "basis": None}
    with pytest.raises(APIError) as error:
        discovery.find_new(STABLE, context_fingerprint=metadata["context_fingerprint"])
    assert error.value.status_code == 409
    assert journey.requests == []


def test_latest_row_before_filter_and_membership_beyond_5000(journey):
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.executemany("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                         "title, doi, guid, decision, run_id) VALUES (2, ?, ?, 'Saved', ?, ?, 'selected', 'test')",
                         [(i, f"feed:g:{i:064x}", f"10.1/kept{i}", str(i)) for i in range(5005)])
        conn.execute("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                     "title, guid, decision, run_id, updated_at) VALUES (3, 43, ?, 'Rejected', 'new', 'user_rejected', 'test', '2099')", (STABLE,))
    rows = read_discovery_membership(journey.paths.triage_db_path)
    assert len(rows) == 5006
    assert next(row for row in rows if row["stable_feed_key"] == STABLE)["decision"] == "user_rejected"
    journey.runtime.zotero_reader = None
    metadata = discovery.discovery_metadata(STABLE)
    assert metadata["admission"]["state"] == "not_saved"
    journey.controls.candidates = [Candidate(title="Different title", doi="10.1/kept5004")]
    # Native presence independently admits this native seed despite app rejection.
    journey.runtime.zotero_reader = journey.reader
    result = begin(journey).json()
    assert result["candidates"] == []
    assert result["discovery"]["retrieval"]["empty_reason"] == "all_known_saved"


def test_native_doi_presence_and_stored_alias_use_current_membership(journey):
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET decision='selected', materialized_zotero_key=NULL")
    assert discovery.discovery_metadata(STABLE)["admission"] == {"state": "saved", "basis": "native"}
    with sqlite3.connect(journey.native_db) as conn:
        conn.execute("INSERT INTO deletedItems(itemID) SELECT itemID FROM items WHERE key='SEED0001'")
    assert discovery.discovery_metadata(STABLE)["admission"]["state"] == "not_saved"
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET decision='user_approved'")
    assert discovery.discovery_metadata(STABLE)["admission"]["basis"] == "user_approved"


def test_native_shared_doi_seed_keeps_all_real_alias_comments_and_revisions(journey):
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET decision='selected', materialized_zotero_key=NULL")
    repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Native alias app context")
    note = add_library_item(journey.native_db, item_key="SHARED01", title="Seed alias note", item_type="note")
    with sqlite3.connect(journey.native_db) as conn:
        native_id = conn.execute("SELECT itemID FROM items WHERE key='SEED0001'").fetchone()[0]
        conn.execute("INSERT INTO itemNotes(itemID,parentItemID,note) VALUES (?,?,'<p>Не повторять native alias.</p>')", (note, native_id))
    metadata = discovery.discovery_metadata(STABLE)
    result = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"], "consent_token": metadata["boundary"]["token"]}).json()
    assert result["discovery"]["assessment"]["status"] == "assessed"
    texts = {r["text"] for r in journey.payloads[0]["seed"]["comments"]["records"]}
    assert texts == {PRIVATE, "Native alias app context", "Не повторять native alias."}
    repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Same-count alias edit")
    assert discovery.discovery_view(session.load(result["id"]))["discovery"]["freshness"] == "stale"


def test_all_family_alias_hits_excluded_unknown_preserved_no_title_merge(journey, monkeypatch):
    add_library_item(journey.native_db, item_key="ALIAS001", title="Different saved metadata", doi="10.1/alias")
    add_library_item(journey.native_db, item_key="TITLE001", title="Same title", doi="10.1/different")
    journey.controls.candidates = [
        Candidate(title="Version family", doi="10.1/miss", family_identifier_keys=["doi:10.1/failed", "doi:10.1/alias"]),
        Candidate(title="Unresolved", doi="10.1/failed"),
        Candidate(title="Same title", doi="10.1/not-saved")]
    original = journey.reader.find_by_external_id

    def lookup(*, doi, arxiv_id):
        if doi == "10.1/failed":
            raise OSError("synthetic lookup failure")
        return original(doi=doi, arxiv_id=arxiv_id)

    monkeypatch.setattr(journey.reader, "find_by_external_id", lookup)
    result = begin(journey).json()
    assert [row["candidate_id"] for row in result["candidates"]] == ["10.1/failed", "10.1/not-saved"]
    assert result["discovery"]["unresolved_membership"] == ["10.1/failed"]
    assert result["discovery"]["recommended_ids"] == ["10.1/not-saved"]


def test_full_native_attribution_and_other_paper_note_not_an_input(journey):
    parent = journey.reader.get_all_items()["items"][0]["item_key"]
    note = add_library_item(journey.native_db, item_key="NOTE0001", title="Note", item_type="note")
    mirror = add_library_item(journey.native_db, item_key="MIRROR01", title="Mirror", item_type="note")
    annotation = add_library_item(journey.native_db, item_key="ANNOT001", title="Annotation", item_type="annotation")
    attachment = add_library_item(journey.native_db, item_key="ATTACH01", title="Attachment", item_type="attachment")
    other = add_library_item(journey.native_db, item_key="OTHER001", title="Other", doi="10.1/other")
    other_note = add_library_item(journey.native_db, item_key="OTHERNOT", title="Other note", item_type="note")
    with sqlite3.connect(journey.native_db) as conn:
        seed = conn.execute("SELECT itemID FROM items WHERE key=?", (parent,)).fetchone()[0]
        conn.executemany("INSERT INTO itemNotes(itemID,parentItemID,note) VALUES (?,?,?)", [
            (note, seed, "<p>Нет. Not this method. " + "полный текст " * 100 + "TAIL_SENTINEL</p>"),
            (mirror, seed, build_user_note_html("owned mirror must not be submitted")),
            (other_note, other, "UNRELATED_PRIVATE_SENTINEL")])
        conn.execute("INSERT INTO itemAttachments(itemID,parentItemID,contentType) VALUES (?,?,'application/pdf')", (attachment, seed))
        conn.execute("INSERT INTO itemAnnotations(itemID,parentItemID,type,text,comment) VALUES (?,?,1,'Highlight is evidence','Не повторять')", (annotation, attachment))
    first = begin(journey, private=True).json()
    records = journey.payloads[0]["seed"]["comments"]["records"]
    assert [r["source"] for r in records] == ["app_note", "native_note", "annotation_comment"]
    assert records[1]["text"].endswith("TAIL_SENTINEL") and records[1]["authorship"] == "uncertain"
    assert records[2]["paper_excerpt"] == "Highlight is evidence"
    assert all("owned mirror" not in r["text"] for r in records)
    assert "UNRELATED_PRIVATE_SENTINEL" not in json.dumps(journey.payloads)
    assert "UNRELATED_PRIVATE_SENTINEL" not in json.dumps(journey.requests)
    with sqlite3.connect(journey.native_db) as conn:
        conn.execute("UPDATE itemNotes SET note='Different unrelated body' WHERE itemID=?", (other_note,))
    assert discovery.discovery_view(session.load(first["id"]))["discovery"]["freshness"] == "current"
    with sqlite3.connect(journey.native_db) as conn:
        conn.execute("INSERT INTO deletedItems(itemID) VALUES (?)", (note,))
    stale = journey.client.get(f"/api/search/{first['id']}").json()
    assert stale["discovery"]["freshness"] == "stale"
    assert stale["discovery"]["seed_comments"] is None
    assert stale["discovery"]["assessment"]["items"] == []
    assert PRIVATE not in json.dumps(stale, ensure_ascii=False)


@pytest.mark.parametrize("status,expected,empty", [
    ("success_empty", "observed", "providers_empty"), ("failed", "failed", None),
    ("not_attempted", "not_attempted", None), ("unknown", "unknown", None)])
def test_receipt_state_not_candidate_count_controls_empty(journey, status, expected, empty):
    journey.controls.provider_status = status
    result = begin(journey).json()["discovery"]
    assert result["retrieval"]["status"] == expected
    assert result["retrieval"]["empty_reason"] == empty


def test_complete_seed_comments_never_clipped_to_fit_model_budget(journey, monkeypatch):
    monkeypatch.setenv("ZS_RELATED_INPUT_CHARS", "1000")
    result = begin(journey, private=True).json()["discovery"]
    assert result["assessment"]["status"] == "budget_exceeded"
    assert result["recommended_ids"] == []
    assert journey.payloads == []


@pytest.mark.parametrize("preference", ["unknown", "conflict"])
def test_typed_preference_and_submitted_vs_cited_ids_are_not_positive_keywords(journey, monkeypatch, preference):
    client = factory.resolve_stage_client(journey.runtime, "deep_review")
    original = client.pydantic_prompt

    def scripted(**kwargs):
        response = original(**kwargs)
        return response.model_copy(update={"items": [row.model_copy(update={
            "preference": preference, "used_context_ids": []}) for row in response.items]})

    monkeypatch.setattr(factory, "resolve_stage_client", lambda *a, **kw: SimpleNamespace(pydantic_prompt=scripted))
    result = begin(journey, private=True).json()["discovery"]
    assert result["assessment"]["status"] == "assessed"
    assert result["assessment"]["submitted_context_ids"]
    assert all(row["used_context_ids"] == [] for row in result["assessment"]["items"])
    assert result["recommended_ids"] == [] and result["comments_in_ranking"] is False
    assert PRIVATE not in json.dumps(journey.requests, ensure_ascii=False)


def test_candidate_binding_ignores_review_order_but_not_public_metadata(journey):
    result = begin(journey).json()
    saved = session.load(result["id"])
    saved.candidates.reverse()
    saved.candidates[0].review = {"brief": {"tldr": "Review-only update"}}
    saved.candidates[0].quality = {"grade": "B"}
    assert discovery.discovery_view(saved)["discovery"]["freshness"] == "current"
    saved.candidates[0].abstract = "Changed public metadata"
    assert discovery.discovery_view(saved)["discovery"]["freshness"] == "stale"


def test_partial_and_missing_receipts_do_not_claim_genuine_empty(journey, monkeypatch):
    original = discovery.federate

    def partial(plan, **kwargs):
        original(plan, **kwargs)
        rows = [row for counts in plan.retrieval_accounting.values() for row in counts["variants"]]
        for row in rows:
            row["outcome"] = {"status": "success_empty", "count": 0, "error_code": None}
        rows[0].pop("outcome")
        return []

    monkeypatch.setattr(discovery, "federate", partial)
    result = begin(journey).json()["discovery"]
    assert result["retrieval"]["status"] == "partial"
    assert result["retrieval"]["empty_reason"] is None


def test_empty_observed_seed_notes_are_not_unavailable(journey):
    repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "")
    result = begin(journey, private=True).json()["discovery"]
    assert result["assessment"]["status"] == "no_comments"
    assert result["seed_comments"]["availability"] == "empty"
    assert journey.payloads == []


@pytest.mark.parametrize("during_build", [False, True])
def test_actual_runtime_client_cache_and_boundary_fence(journey, monkeypatch, during_build):
    from zotero_summarizer.models import AppState
    from zotero_summarizer.runtime import RuntimeState

    inert = factory.resolve_stage_client(journey.runtime, "deep_review")
    monkeypatch.setattr(factory, "resolve_stage_client", resolve_stage_client)
    old_calls, built = [], []
    old_client = SimpleNamespace(pydantic_prompt=lambda **kw: old_calls.append(kw))
    app = RuntimeState(app_state=AppState(config=journey.config), zotero_reader=journey.reader)

    def build(resolved):
        built.append(resolved.model)
        if resolved.model == "synthetic":
            return old_client
        if during_build:
            journey.config.llm_routing.default.model = "unconsented-third-model"
        return inert

    monkeypatch.setattr(factory, "build_client_for_stage", build)
    monkeypatch.setattr(discovery, "state", lambda: app)
    monkeypatch.setattr(semantic, "state", lambda: app)
    assert factory.resolve_stage_client(app, "deep_review") is old_client
    journey.config.llm_routing.default.model = "consented-replacement"
    result = begin(journey, private=True).json()["discovery"]
    assert built == ["synthetic", "consented-replacement"]
    assert old_calls == []
    if during_build:
        assert journey.payloads == []
        assert result["freshness"] == "stale"
    else:
        assert len(journey.payloads) == 1
        assert result["assessment"]["status"] == "assessed"


def test_durable_only_sentinel_never_crosses_any_session_http_response(journey, monkeypatch):
    found = begin(journey, private=True).json()
    saved = session.load(found["id"])
    sentinel = "DURABLE_ONLY_PRIVATE_SENTINEL"
    saved.discovery["snapshot"]["internal_private_marker"] = sentinel
    saved.discovery["attempts"].append({"private_attempt_marker": sentinel})
    session.save(saved)
    assert sentinel in session._path(saved.id).read_text()
    assert sentinel not in json.dumps(saved.to_dict())
    assert saved.to_dict()["discovery"]["new_actions_allowed"] is False
    with pytest.raises(ValueError):
        ResearchSession.from_dict(saved.to_dict())  # A wire clone cannot become ordinary Search.
    monkeypatch.setattr(search, "default_deps", lambda: object())
    monkeypatch.setattr(search, "run_screen", lambda *a, **kw: saved)
    monkeypatch.setattr(search, "_kickoff_review", lambda sid, **kwargs: False)
    responses = [journey.client.get(f"/api/search/{saved.id}"), begin(journey, private=True),
                 journey.client.get("/api/search"),
                 journey.client.post("/api/search/screen", json={"query": "public transport oracle"}),
                 journey.client.post(f"/api/search/{saved.id}/review")]
    for response in responses:
        assert response.status_code == 200
        assert sentinel not in response.text
    for response in (responses[0], responses[1], responses[3]):
        assert "snapshot" not in response.json()["discovery"]
        assert response.json()["discovery"]["seed_comments"]["records"]  # Intentional current projection.
    repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Changed context")
    stale = journey.client.get(f"/api/search/{saved.id}").json()
    assert stale["discovery"]["seed_comments"] is None
    assert stale["discovery"]["assessment"]["items"] == []
    assert PRIVATE not in json.dumps(stale, ensure_ascii=False)
    monkeypatch.setattr(journey.reader, "get_field_values", lambda *a: (_ for _ in ()).throw(OSError("unavailable")))
    unverified = journey.client.get(f"/api/search/{saved.id}").json()
    assert unverified["discovery"]["freshness"] == "unverified"
    assert unverified["discovery"]["seed_comments"] is None
    assert unverified["discovery"]["assessment"]["submitted_context_ids"] == []
    assert sentinel not in json.dumps(unverified) and PRIVATE not in json.dumps(unverified, ensure_ascii=False)
    assert journey.client.delete(f"/api/search/{saved.id}").json() == {"deleted": saved.id}
