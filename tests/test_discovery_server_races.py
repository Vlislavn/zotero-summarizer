"""Ownership/effect oracles against real routes and isolated session files; UNRUN."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from tests.test_discovery_server import PRIVATE, begin, journey  # noqa: F401 — shared fixture
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import search
from zotero_summarizer.services.search import discovery, materialize, pipeline, session
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.search._models import QueryPlan, SearchIntent
from zotero_summarizer.storage import repositories


def request(journey, *, private=True, retry=False):
    metadata = discovery.discovery_metadata("SEED0001")
    return dict(context_fingerprint=metadata["context_fingerprint"],
                consent_token=metadata["boundary"]["token"] if private else None, retry=retry)


def test_concurrent_repeat_coalesces_and_get_does_not_interrupt_live_owner(journey):
    entered, release = Event(), Event()
    kwargs = request(journey)
    journey.controls.provider_hook = lambda: (entered.set(), release.wait(5))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(discovery.find_new, "SEED0001", **kwargs)
        assert entered.wait(5)
        running = session.find_discovery(kwargs["context_fingerprint"])
        view = discovery.discovery_view(running)
        assert view["status"] == "discovery_running"
        assert view["discovery"]["seed_comments"] is None
        assert view["discovery"]["recommended_ids"] == []
        assert view["discovery"]["assessment"]["items"] == []
        second = pool.submit(discovery.find_new, "SEED0001", **kwargs)
        release.set()
        one, two = first.result(5), second.result(5)
    assert one.id == two.id
    assert len(journey.requests) == len(journey.payloads) == 1


@pytest.mark.parametrize("change", ["note", "route", "membership"])
def test_provider_held_change_makes_zero_obsolete_private_calls(journey, change):
    entered, release = Event(), Event()
    kwargs = request(journey)
    journey.controls.provider_hook = lambda: (entered.set(), release.wait(5))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(discovery.find_new, "SEED0001", **kwargs)
        assert entered.wait(5)
        if change == "note":
            repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Changed private text")
        elif change == "route":
            journey.config.llm_routing.default.model = "replacement"
        else:
            with sqlite3.connect(journey.paths.triage_db_path) as conn:
                conn.execute("UPDATE processed_feed_items SET decision='selected'")
        release.set()
        result = discovery.discovery_view(future.result(5))
    assert result["discovery"]["freshness"] == "stale"
    assert result["discovery"]["assessment"]["items"] == []
    assert journey.payloads == []


def test_assessment_lock_rechecks_before_actual_client_resolution(journey, monkeypatch):
    waiting = Event()
    original = discovery._assess
    calls = []
    resolver = factory.resolve_stage_client

    def assess(*args):
        waiting.set()
        return original(*args)

    monkeypatch.setattr(discovery, "_assess", assess)
    monkeypatch.setattr(factory, "resolve_stage_client", lambda *a, **kw: (calls.append(kw), resolver(*a, **kw))[1])
    kwargs = request(journey)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with session._lock_for("related-assessment"):
            future = pool.submit(discovery.find_new, "SEED0001", **kwargs)
            assert waiting.wait(5)
            repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Changed while waiting for lock")
        result = discovery.discovery_view(future.result(5))
    assert calls == journey.payloads == []
    assert result["discovery"]["freshness"] == "stale"


def test_model_held_change_hides_all_private_evidence(journey):
    journey.controls.model_hook = lambda: repositories.upsert_review_note(
        journey.paths.triage_db_path, "SEED0001", "New note after request was submitted")
    result = begin(journey, private=True).json()
    assert len(journey.payloads) == 1
    assert result["discovery"]["freshness"] == "stale"
    assert result["discovery"]["new_actions_allowed"] is False
    assert result["discovery"]["seed_comments"] is None
    assert result["discovery"]["assessment"]["items"] == []
    assert result["discovery"]["assessment"]["submitted_context_ids"] == []
    assert PRIVATE not in json.dumps(result, ensure_ascii=False)


def test_failures_reuse_and_explicit_assessment_retry_reuses_public_pool(journey):
    journey.controls.fail_model = True
    first = begin(journey, private=True).json()
    again = begin(journey, private=True).json()
    assert first["id"] == again["id"]
    assert first["discovery"]["assessment"]["status"] == "failed"
    assert len(journey.requests) == 1
    journey.controls.fail_model = False
    retried = begin(journey, private=True, retry=True).json()
    assert retried["id"] == first["id"]
    assert len(journey.requests) == len(journey.payloads) == 1
    assert retried["discovery"]["assessment"]["status"] == "assessed"
    assert session.load(first["id"]).discovery["attempts"][0]["assessment"]["status"] == "failed"


def test_assessment_upgrade_hides_prior_pool_until_discovery_completes(journey):
    first = begin(journey).json()
    assert first["discovery"]["recommended_ids"]
    entered, release = Event(), Event()
    journey.controls.model_hook = lambda: (entered.set(), release.wait(5))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(discovery.find_new, "SEED0001", **request(journey))
        assert entered.wait(5)
        try:
            running = journey.client.get(f"/api/search/{first['id']}").json()
            assert running["status"] == "discovery_running"
            assert running["discovery"]["freshness"] == "current"
            assert running["discovery"]["seed_comments"] is None
            assert running["discovery"]["recommended_ids"] == []
            assert running["discovery"]["assessment"]["items"] == []
        finally:
            release.set()
        finished = discovery.discovery_view(future.result(5))
    assert finished["status"] == "discovery_ready"
    assert finished["discovery"]["assessment"]["status"] == "assessed"
    assert finished["discovery"]["seed_comments"]["records"]
    assert finished["discovery"]["recommended_ids"]


def test_failed_providers_require_explicit_retry_and_keep_prior_receipts(journey):
    journey.controls.provider_status = "failed"
    first = begin(journey).json()
    assert begin(journey).json()["id"] == first["id"]
    assert len(journey.requests) == 1
    assert first["discovery"]["seed_comments"] is None
    assert first["discovery"]["recommended_ids"] == []
    journey.controls.provider_status = "success_nonempty"
    second = begin(journey, retry=True).json()
    assert second["id"] == first["id"] and len(journey.requests) == 2
    assert session.load(first["id"]).discovery["attempts"][0]["retrieval"]["status"] == "failed"


@pytest.mark.parametrize("status", ["discovery_pending", "discovery_running"])
def test_restart_projection_interrupts_without_work_and_retry_reuses_pool(journey, status):
    result = begin(journey).json()
    saved = session.load(result["id"])
    saved.status = status
    session.save(saved)
    assert journey.client.get(f"/api/search/{saved.id}").json()["status"] == "discovery_interrupted"
    assert session.load(saved.id).status == status  # GET does not write a recovery claim.
    interrupted = begin(journey).json()
    assert interrupted["status"] == "discovery_interrupted"
    assert interrupted["discovery"]["assessment"]["status"] == "interrupted"
    assert len(journey.requests) == 1
    recovered = begin(journey, private=True, retry=True).json()
    assert recovered["status"] == "discovery_ready"
    assert len(journey.requests) == len(journey.payloads) == 1


def test_strict_corrupt_lookup_and_multiple_match_do_not_start_work(journey):
    result = begin(journey).json()
    fp = result["discovery"]["context_fingerprint"]
    path = session._path(result["id"])
    original = path.read_text()
    for corrupt in ("{not-json", json.dumps({**json.loads(original), "discovery": {"version": 1}})):
        path.write_text(corrupt)
        with pytest.raises((ValueError, KeyError)):
            discovery.find_new("SEED0001", **request(journey, private=False))
    path.write_text(original)
    clone = session.load(result["id"])
    clone.id = f"discovery_{fp}_" + "b" * 12
    session.save(clone)
    with pytest.raises(APIError) as conflict:
        session.find_discovery(fp)
    assert conflict.value.error == "discovery_identity_conflict"
    assert len(journey.requests) == 1
    session.delete(clone.id)
    assert session.find_discovery(fp).id == result["id"]


def test_missing_lookup_file_without_tombstone_is_not_a_cache_miss(journey, monkeypatch):
    result = begin(journey).json()
    fp = result["discovery"]["context_fingerprint"]
    monkeypatch.setattr(session, "load", lambda *a: (_ for _ in ()).throw(FileNotFoundError("disappeared")))
    with pytest.raises(FileNotFoundError):
        session.find_discovery(fp)
    assert len(journey.requests) == 1


def test_delete_during_provider_work_never_resurrects_or_assesses(journey):
    entered, release = Event(), Event()
    kwargs = request(journey)
    journey.controls.provider_hook = lambda: (entered.set(), release.wait(5))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(discovery.find_new, "SEED0001", **kwargs)
        assert entered.wait(5)
        live = session.find_discovery(kwargs["context_fingerprint"])
        session.delete(live.id)
        release.set()
        with pytest.raises(APIError) as deleted:
            future.result(5)
    assert deleted.value.error == "discovery_deleted"
    assert not session._path(live.id).exists()
    assert journey.payloads == []
    journey.controls.provider_hook = None
    replacement = discovery.find_new("SEED0001", **kwargs)
    assert replacement.id != live.id


def test_explicit_review_cas_no_agentic_refinement_and_direct_run_fence(journey, monkeypatch):
    result = begin(journey, private=True).json()
    with pytest.raises(APIError) as unclaimed:
        pipeline.run_review(result["id"], deps=SimpleNamespace())
    assert unclaimed.value.error == "explicit_review_required"
    starts, reviewed = [], []
    monkeypatch.setattr(search, "threading", SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: starts.append(kw))))
    monkeypatch.setattr(search, "default_deps", lambda: object())
    monkeypatch.setattr(search, "agentic_enabled", lambda: True)
    monkeypatch.setattr(search, "run_agentic_rounds", lambda *a, **kw: pytest.fail("discovery refinement"))
    monkeypatch.setattr(search, "run_review", lambda sid, **kw: reviewed.append(sid))
    kickoff = journey.client.post(f"/api/search/{result['id']}/review")
    assert kickoff.json()["accepted"] is True and PRIVATE not in kickoff.text
    repeated = journey.client.post(f"/api/search/{result['id']}/review")
    assert repeated.json()["accepted"] is False and PRIVATE not in repeated.text
    assert len(starts) == 1
    search._review_worker(result["id"])
    assert reviewed == [result["id"]]


def test_stale_review_and_new_add_409_before_effect_but_old_add_replay_preserved(journey, monkeypatch):
    result = begin(journey).json()
    monkeypatch.setattr(materialize, "ZoteroWriter", lambda *a: pytest.fail("native effect before stale rejection"))
    monkeypatch.setattr(materialize, "_resolve_collection_name", lambda key: "Inbox")
    repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Changed input")
    assert journey.client.post(f"/api/search/{result['id']}/review").status_code == 409
    new_add = journey.client.post(f"/api/search/{result['id']}/materialize", json={"candidate_id": "10.1/new"})
    assert new_add.status_code == 409
    saved = session.load(result["id"])
    worker_copy = deepcopy(saved)
    saved.candidates[0].materialized_zotero_key = "ADDED001"
    session.save(saved)
    session.save_merge(worker_copy)
    old_add = journey.client.post(f"/api/search/{result['id']}/materialize", json={"candidate_id": "10.1/new"})
    assert old_add.json() == {"status": "already_added", "zotero_key": "ADDED001"}
    assert session.load(result["id"]).candidates[0].materialized_zotero_key == "ADDED001"
    assert len(journey.requests) == 1 and journey.payloads == []


def test_new_add_changes_membership_and_next_get_is_stale(journey, monkeypatch):
    result = begin(journey).json()
    effects, receipts = [], []
    actual = materialize.ZoteroWriter.apply_feed_materialization

    def observed(writer, **kwargs):
        assert writer.db_path == journey.native_db
        effects.append(deepcopy(kwargs))
        receipt = actual(writer, **kwargs)
        receipts.append(receipt)
        return receipt

    monkeypatch.setattr(materialize, "settings", lambda: journey.paths)
    monkeypatch.setattr(materialize.ZoteroWriter, "is_connector_running", lambda self: False)
    monkeypatch.setattr(materialize.ZoteroWriter, "apply_feed_materialization", observed)
    added = journey.client.post(f"/api/search/{result['id']}/materialize", json={"candidate_id": "10.1/new"})
    assert added.status_code == 200 and added.json()["status"] == "added"
    assert len(effects) == len(receipts) == 1 and PRIVATE not in json.dumps(effects, ensure_ascii=False)
    key = added.json()["zotero_key"]
    assert receipts[0]["item_key"] == key and receipts[0]["applied_steps"]
    backup = Path(receipts[0]["backup_path"])
    assert backup.parent == journey.native_db.parent and backup.is_file()
    with sqlite3.connect(f"{backup.as_uri()}?mode=ro&immutable=1", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM items WHERE key=?", (key,)).fetchone() == (0,)
    detail = journey.reader.get_item_detail(key)
    assert detail["doi"] == "10.1/new" and detail["title"] == effects[0]["feed_payload"]["title"]
    after = journey.client.get(f"/api/search/{result['id']}").json()["discovery"]
    assert after["freshness"] == "stale" and after["new_actions_allowed"] is False
    assert journey.client.post(f"/api/search/{result['id']}/materialize", json={"candidate_id": "10.1/new"}).json()["status"] == "already_added"
    assert len(effects) == len(receipts) == 1
    assert list(journey.native_db.parent.glob("zotero.sqlite.backup_*")) == [backup]


def test_ordinary_search_autostart_and_agentic_order_are_preserved(journey, monkeypatch):
    ordinary = session.new_session(raw_query="ordinary topic", intent=SearchIntent(raw_query="ordinary topic"),
                                   plan=QueryPlan(), questions=[])
    ordinary.status = "screened"
    session.save(ordinary)
    events = []
    monkeypatch.setattr(search, "default_deps", lambda: object())
    monkeypatch.setattr(search, "run_screen", lambda *a, **kw: ordinary)
    monkeypatch.setattr(search, "threading", SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: events.append("start"))))
    response = journey.client.post("/api/search/screen", json={"query": "ordinary topic"})
    assert response.json()["status"] == "reviewing" and events == ["start"]
    monkeypatch.setattr(search, "agentic_enabled", lambda: True)
    monkeypatch.setattr(search, "run_agentic_rounds", lambda *a, **kw: events.append("refine"))
    monkeypatch.setattr(search, "run_review", lambda *a, **kw: events.append("review"))
    search._review_worker(ordinary.id)
    assert events == ["start", "refine", "review"]


def test_discovery_cannot_enter_ordinary_automatic_review_kickoff(journey, monkeypatch):
    result = begin(journey, private=True).json()
    saved = session.load(result["id"])
    monkeypatch.setattr(search, "threading", SimpleNamespace(Thread=lambda **kw: pytest.fail("automatic discovery review")))
    with pytest.raises(APIError) as blocked:
        search._kickoff_review(saved.id)
    assert blocked.value.error == "explicit_review_required"
    assert session.load(saved.id).status == "discovery_ready"
    monkeypatch.setattr(search, "default_deps", lambda: object())
    monkeypatch.setattr(search, "run_screen", lambda *a, **kw: saved)
    response = journey.client.post("/api/search/screen", json={"query": "public alternate-action oracle"})
    assert response.status_code == 409 and PRIVATE not in response.text
    assert session.load(saved.id).status == "discovery_ready"
    assert len(journey.requests) == len(journey.payloads) == 1


def test_unclaimed_discovery_worker_rejects_before_review_client_construction(journey, monkeypatch):
    result = begin(journey).json()
    monkeypatch.setattr(search, "default_deps", lambda: pytest.fail("unclaimed review client construction"))
    with pytest.raises(APIError) as blocked:
        search._review_worker(result["id"])
    assert blocked.value.error == "explicit_review_required"
    assert len(journey.requests) == 1 and journey.payloads == []


@pytest.mark.parametrize("suffix", ["refine", "clone"])
@pytest.mark.parametrize("stale", [False, True])
def test_frozen_base_has_no_alternate_refine_or_clone_http_action(journey, suffix, stale):
    result = begin(journey, private=True).json()
    before = session._path(result["id"]).read_text()
    if stale:
        repositories.upsert_review_note(journey.paths.triage_db_path, "SEED0001", "Stale alternate-action input")
    response = journey.client.post(f"/api/search/{result['id']}/{suffix}", json={"force": True, "query": PRIVATE})
    assert response.status_code == 404 and PRIVATE not in response.text
    assert session._path(result["id"]).read_text() == before
    assert len(journey.requests) == len(journey.payloads) == 1
