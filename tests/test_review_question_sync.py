"""Immutable question creates reuse sync receipts without manual-field conflicts."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_offline_sync import _db, _mutation
from tests.test_review_note_identity import STABLE, _feed, _materialize
from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import golden, library, sync
from zotero_summarizer.services.library import review_questions as online_questions
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import repositories, review_note_projection, review_questions


def _question(key="PAPER001", question="  Why?\n", **extra):
    submission_id = str(uuid4())
    payload = {"submission_id": submission_id, "item_key": key, "question": question,
               "origin": "typed", "preset_id": None}
    return _mutation(key, "review_question", payload, 0, mutation_id=submission_id,
                     operation="create", created_at="2026-10-08T12:00:00Z", **extra)


def _state(db):
    with sqlite3.connect(db) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("review_questions", "review_notes", "sync_changes",
                              "sync_mutations", "review_note_mirror_intents", "label_verdicts")}


def _saved(db, mutation):
    with sqlite3.connect(db) as conn:
        row, _ = review_questions.save(conn, mutation["value"])
    return row


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(sync, "_db_path", lambda: db)
    monkeypatch.setattr(golden, "_db_path", lambda: db)
    monkeypatch.setattr(golden, "_load_all", lambda: [])
    monkeypatch.setattr(golden, "_golden_csv_path", lambda: tmp_path / "missing.csv")
    monkeypatch.setattr(golden, "_build_source_payload", lambda _: None)
    monkeypatch.setattr(online_questions, "settings", lambda: SimpleNamespace(triage_db_path=db))
    monkeypatch.setattr(service.reading_queue, "build_reading_queue", lambda **_: {"items": []})
    monkeypatch.setattr(service.deep_review, "current_reviews", lambda: {})
    mirrors = []

    def mirror(path, key):
        assert path == db
        assert _state(db)["sync_mutations"]
        mirrors.append(key)
        return {"note_written": False, "note_error": None}

    def forbidden(*_, **__):
        raise AssertionError("question reached verdict/training/QA/materialization effects")

    monkeypatch.setattr(service.verdict_effects, "mirror_review_note", mirror)
    monkeypatch.setattr(service.verdict_effects, "apply_verdict_effects", forbidden)
    monkeypatch.setattr(service.verdict_effects, "mirror_current_verdict", forbidden)
    monkeypatch.setattr(service.label_verdicts, "log_committed_transition", forbidden)
    monkeypatch.setattr(service, "require_feed_review", forbidden)
    monkeypatch.setattr(library.qa, "ask_paper", forbidden)
    app = FastAPI()
    install_error_handlers(app)
    for route in (sync.router, golden.router, library.router):
        app.include_router(route)
    with TestClient(app) as http:
        yield http, db, mirrors


def _push(http, mutations, predecessors=()):
    return http.post("/api/sync/push", json={
        "protocol": 1, "mutations": mutations, "predecessors": list(predecessors),
    })


def test_http_create_retry_and_deliberate_repeat_keep_manual_text_separate(boundary):
    http, db, mirrors = boundary
    repositories.upsert_review_note(db, "PAPER001", "manual text")
    mutation = _question()

    response = _push(http, [mutation])

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "applied"
    row = result["canonical"]["value"]
    assert {key: row[key] for key in mutation["value"]} == mutation["value"]
    assert row["submitted_at"]
    assert result["question_mirror"] == {"note_written": False, "note_error": None}
    before = _state(db)
    retry = _push(http, [mutation]).json()["results"][0]
    assert retry == {**result, "status": "already_applied"}
    assert _state(db) == before
    repeat = _question(question=mutation["value"]["question"])
    assert _push(http, [repeat]).json()["results"][0]["status"] == "applied"
    assert len(_state(db)["review_questions"]) == 2
    assert repositories.get_review_note(db, "PAPER001") == "manual text"
    paper = http.get("/api/sync/pull?protocol=1").json()["papers"][0]
    assert paper["review_note"] == "manual text"
    assert len(paper["questions"]) == 2
    assert paper["verdict"] is None
    assert _state(db)["label_verdicts"] == []
    assert mirrors == ["PAPER001"] * 3


def test_online_save_then_sync_gets_receipt_without_second_row_journal_or_intent(boundary, monkeypatch):
    http, db, mirrors = boundary
    mutation = _question()
    online_mirrors = []

    def online_mirror(key):
        saved = _state(db)
        assert len(saved["review_questions"]) == 1
        assert len(saved["sync_changes"]) == 1
        assert len(saved["review_note_mirror_intents"]) == 1
        assert saved["sync_mutations"] == []
        online_mirrors.append(key)
        with closing(sqlite3.connect(db)) as conn:
            desired = review_note_projection.snapshot(conn, key)
        return {"note_written": False, "note_error": None, "note_pending": True,
                "projection_revision": desired["revision"], "target_key": desired["target_key"]}

    monkeypatch.setattr(online_questions, "mirror_question", online_mirror)
    online = http.post("/api/library/questions", json=mutation["value"])
    assert online.status_code == 200, online.text
    before = _state(db)

    result = _push(http, [mutation]).json()["results"][0]

    assert result["status"] == "applied"
    assert result["canonical"]["value"] == online.json()["question_submission"]
    after = _state(db)
    assert len(after["sync_mutations"]) == 1
    assert all(after[table] == rows for table, rows in before.items() if table != "sync_mutations")
    assert result["applied_revision"] == before["sync_changes"][0][0]
    assert _push(http, [mutation]).json()["results"][0]["status"] == "already_applied"
    assert online_mirrors == [mutation["item_key"]]
    assert mirrors == [mutation["item_key"]] * 2


@pytest.mark.parametrize("online_first", [False, True])
@pytest.mark.parametrize("changed", ["question", "item_key", "origin", "preset_id"])
def test_changed_same_uuid_is_rejected_without_alias_or_payload_rewrite(boundary, online_first, changed):
    http, db, mirrors = boundary
    _feed(db)
    _materialize(db)
    mutation = _question("feed:42")
    if online_first:
        _saved(db, mutation)
    else:
        assert _push(http, [mutation]).json()["results"][0]["status"] == "applied"
    changed_mutation = deepcopy(mutation)
    changes = {"question": "different", "item_key": "PAPER001", "origin": "preset", "preset_id": "preset"}
    changed_mutation["value"][changed] = changes[changed]
    if changed == "item_key":
        changed_mutation["item_key"] = "PAPER001"
    if changed == "origin":
        changed_mutation["value"]["preset_id"] = "preset"
    before, calls = _state(db), list(mirrors)

    result = _push(http, [changed_mutation]).json()["results"][0]

    assert result["status"] == "rejected"
    assert result["error"]
    assert _state(db) == before
    assert mirrors == calls


@pytest.mark.parametrize("key", [" PAPER001 ", " . ", "feed:42", "k" * 501])
def test_http_keeps_accepted_key_body_preset_and_unicode_bound_exact(boundary, key):
    http, db, _ = boundary
    text = "😀" * 1998 + "e\u0301"
    mutation = _question(key, text)
    mutation["value"].update(origin="preset", preset_id="  " + "p" * 700 + "  ")
    mutation["device_id"] = "  ipad-1  "

    response = _push(http, [mutation])

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "applied"
    assert {key: result["canonical"]["value"][key] for key in mutation["value"]} == mutation["value"]
    with sqlite3.connect(db) as conn:
        device, stored = conn.execute("SELECT device_id, request_json FROM sync_mutations").fetchone()
    assert device == "ipad-1"
    assert json.loads(stored)["value"] == mutation["value"]
    assert json.loads(stored)["item_key"] == key


_INVALID = [
    ("value", None), ("value", "text"), ("operation", "set"),
    ("item_key", "mismatched"), ("mutation_id", str(uuid4())),
    ("value.submission_id", "not-a-uuid"), ("value.question", "  "),
    ("value.question", "😀" * 2001), ("value.question", 42),
    ("value.origin", "answer"), ("value.preset_id", "  "),
    ("value.item_key", None), ("value.item_key", ""), ("value.item_key", "."),
    ("value.item_key", ".."), ("value.item_key", "a/b"), ("value.item_key", "a\\b"),
    ("value.item_key", "a\0b"), ("value.item_key", True),
    ("value.item_key", []), ("value.item_key", {}),
    ("value.origin", "preset"), ("resolves_mutation_id", str(uuid4())),
]


def _invalid(mutation, field, value):
    if field.startswith("value."):
        key = field.split(".")[1]
        mutation["value"][key] = value
        if key == "item_key":
            mutation["item_key"] = value
    else:
        mutation[field] = value
    return mutation


@pytest.mark.parametrize("field,value", _INVALID)
def test_http_and_direct_service_invalid_question_never_writes(boundary, field, value):
    http, db, mirrors = boundary
    mutation = _invalid(_question(), field, value)
    before = _state(db)

    response = _push(http, [mutation])
    assert response.status_code == 422, response.text
    direct = service.push(db, [mutation])["results"][0]
    assert direct["status"] == "rejected"
    with pytest.raises(ValueError):
        repositories.apply_sync_mutation(db, mutation)
    assert _state(db) == before
    assert mirrors == []


def test_concurrent_distinct_uuids_ignore_shared_manual_and_question_base(tmp_path):
    db = _db(tmp_path)
    repositories.upsert_review_note(db, "PAPER001", "manual")
    mutations = [_question(question="same deliberate text") for _ in range(2)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda row: repositories.apply_sync_mutation(db, row), mutations))

    assert [result["status"] for result in results] == ["applied", "applied"]
    assert len({result["applied_revision"] for result in results}) == 2
    assert len(_state(db)["review_questions"]) == 2
    assert len(_state(db)["sync_mutations"]) == 2
    assert repositories.get_review_note(db, "PAPER001") == "manual"


def test_concurrent_same_uuid_has_one_receipt_and_question(tmp_path):
    db = _db(tmp_path)
    mutation = _question()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repositories.apply_sync_mutation(db, mutation), range(2)))
    assert sorted(row["status"] for row in results) == ["already_applied", "applied"]
    state = _state(db)
    assert len(state["review_questions"]) == len(state["sync_changes"]) == len(state["sync_mutations"]) == 1


@pytest.mark.parametrize("failure", ["question", "intent", "receipt"])
def test_failed_transaction_rolls_back_question_journal_intent_and_receipt(tmp_path, failure):
    db = _db(tmp_path)
    table = {"question": "review_questions", "intent": "review_note_mirror_intents", "receipt": "sync_mutations"}[failure]
    with sqlite3.connect(db) as conn:
        conn.execute(f"CREATE TRIGGER injected BEFORE INSERT ON {table} "
                     "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    before = _state(db)

    with pytest.raises(sqlite3.IntegrityError):
        repositories.apply_sync_mutation(db, _question())

    assert _state(db) == before


@pytest.mark.parametrize("source", ["missing", "malformed", "unavailable", "present"])
def test_alias_question_only_detail_and_pull_are_readonly_separate_and_honest(boundary, monkeypatch, source):
    http, db, _ = boundary
    _feed(db)
    _materialize(db)
    mutation = _question("feed:42")
    row = _saved(db, mutation)
    repositories.upsert_review_note(db, STABLE, "manual only")

    def payload(_):
        if source == "malformed":
            raise golden.review_detail_svc.InvalidItemKey("source key unsupported")
        if source == "unavailable":
            raise APIError("zotero_unavailable", "not configured", 503)
        return {"title": "present", "has_pdf": False, "deep_review": None} if source == "present" else None

    monkeypatch.setattr(golden, "_build_source_payload", payload)
    before = _state(db)
    fields = repositories.sync_current_fields(db)
    initial = http.get("/api/sync/pull?protocol=1").json()
    assert initial["cursor"] == repositories.sync_status(db)["cursor"]
    assert initial["changes"] == []
    papers = {paper["item_key"]: paper for paper in initial["papers"]}
    for key in ("feed:42", STABLE, "PAPER001"):
        assert fields[(key, "review_question")]["value"] == [row]
        assert papers[key]["questions"] == [row]
        assert papers[key]["review_note"] == "manual only"
        assert papers[key]["revisions"]["review_question"] > 0
        detail = http.get("/api/golden/review-detail", params={"item_key": key})
        assert detail.status_code == 200, detail.text
        assert detail.json()["questions"] == [row]
        assert detail.json()["user_note"] == "manual only"
        assert detail.json()["has_pdf"] is False
        assert detail.json()["deep_review"] is None
        assert detail.json()["verdict"] is None
    assert _state(db) == before


def test_question_only_unsupported_source_key_and_exact_padded_lookup(boundary, monkeypatch):
    http, db, _ = boundary
    row = _saved(db, _question("feed:unsupported"))
    padded = _saved(db, _question(" PADDED "))
    def source(key):
        if key.startswith("feed:"):
            raise golden.review_detail_svc.InvalidItemKey("not a source id")
        return None
    monkeypatch.setattr(golden, "_build_source_payload", source)
    for key, expected in [("feed:unsupported", row), (" PADDED ", padded)]:
        detail = http.get("/api/golden/review-detail", params={"item_key": key})
        assert detail.status_code == 200, detail.text
        assert detail.json()["questions"] == [expected]
        assert detail.json()["user_note"] == ""
        assert detail.json()["deep_review"] is None
    assert http.get("/api/golden/review-detail", params={"item_key": "feed:other"}).status_code == 422
    assert http.get("/api/golden/review-detail", params={"item_key": "UNKNOWN"}).status_code == 404
    papers = {p["item_key"]: p for p in http.get("/api/sync/pull?protocol=1").json()["papers"]}
    assert set(papers) == {"feed:unsupported", " PADDED "}
    for key, expected in [("feed:unsupported", row), (" PADDED ", padded)]:
        assert papers[key]["questions"] == [expected]
        assert papers[key]["review_note"] is None


def test_receipt_lookup_and_predecessors_keep_original_question_request(boundary):
    http, db, _ = boundary
    mutation = _question(" PAPER001 ")
    first = _push(http, [mutation]).json()["results"][0]
    assert first["status"] == "applied"
    revisions = repositories.sync_applied_revisions(db, [mutation["mutation_id"]])
    assert revisions == {("ipad-1", " PAPER001 ", "review_question"): first["applied_revision"]}
    next_question = _question(" PAPER001 ")
    assert _push(http, [next_question], [mutation["mutation_id"]]).json()["results"][0]["status"] == "applied"
    with sqlite3.connect(db) as conn:
        request_json, result_json = conn.execute(
            "SELECT request_json, result_json FROM sync_mutations WHERE mutation_id=?", (mutation["mutation_id"],)
        ).fetchone()
    assert json.loads(request_json) == mutation
    assert "question_mirror" not in json.loads(result_json)
    before = _state(db)
    assert _push(http, [mutation], [next_question["mutation_id"]]).json()["results"][0]["status"] == "already_applied"
    assert _state(db) == before


def test_mirror_failure_is_postcommit_and_retry_uses_same_entrypoint(boundary, monkeypatch):
    http, db, mirrors = boundary
    mutation = _question()
    original = service.verdict_effects.mirror_review_note
    def failure(*_):
        assert _state(db)["sync_mutations"]
        raise RuntimeError("delivery failed after commit")
    monkeypatch.setattr(service.verdict_effects, "mirror_review_note", failure)
    with pytest.raises(RuntimeError):
        service.push(db, [mutation])
    before = _state(db)
    assert len(before["review_questions"]) == len(before["sync_mutations"]) == 1
    monkeypatch.setattr(service.verdict_effects, "mirror_review_note", original)
    assert _push(http, [mutation]).json()["results"][0]["status"] == "already_applied"
    assert _state(db) == before
    assert mirrors == ["PAPER001"]


def test_question_revisions_do_not_conflict_with_manual_edits_or_clears(boundary):
    http, db, _ = boundary
    question = _question()
    assert _push(http, [question]).json()["results"][0]["status"] == "applied"
    note = _mutation("PAPER001", "review_note", "stale manual draft", 0)
    applied = _push(http, [note]).json()["results"][0]
    assert applied["status"] == "applied"
    row = _state(db)["review_questions"]
    clear = _mutation("PAPER001", "review_note", None, applied["applied_revision"], operation="delete")
    assert _push(http, [clear]).json()["results"][0]["status"] == "applied"
    assert _state(db)["review_questions"] == row
    paper = http.get("/api/sync/pull?protocol=1").json()["papers"][0]
    assert paper["review_note"] is None
    assert len(paper["questions"]) == 1


@pytest.mark.parametrize("field,value", [
    ("item_key", "k" * 501), ("value", "n" * 50_001), ("value", {}),
    ("device_id", "d" * 101), ("comment", "c" * 10_001),
    ("model_priority", "p" * 51), ("operation", "create"),
])
def test_manual_scalar_bounds_and_operations_are_preserved(boundary, field, value):
    http, db, mirrors = boundary
    mutation = _mutation("PAPER001", "review_note", "manual", 0)
    mutation[field] = value
    before = _state(db)
    response = _push(http, [mutation])
    assert response.status_code == 422, response.text
    assert _state(db) == before
    assert mirrors == []


def test_manual_identity_trim_and_maximum_value_remain_unchanged(boundary):
    http, db, mirrors = boundary
    note = "  " + "n" * 49_996 + "  "
    mutation = _mutation(" PAPER001 ", "review_note", note, 0, device_id=" ipad-1 ")
    response = _push(http, [mutation])
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "applied"
    assert result["canonical"] == {"value": note}
    assert "question_mirror" not in result
    assert repositories.get_review_note(db, "PAPER001") == note
    assert mirrors == ["PAPER001"]


@pytest.mark.parametrize("operation", ["set", "delete"])
@pytest.mark.parametrize("error", ["synthetic external failure", "synthetic acknowledgement failure"])
def test_manual_mirror_failure_metadata_is_http200_pending_and_current_on_replay(boundary, monkeypatch, operation, error):
    http, db, _ = boundary
    repositories.upsert_review_note(db, "PAPER001", "original manual")
    base = repositories.sync_current_fields(db)[("PAPER001", "review_note")]["revision"]
    value = "new manual draft" if operation == "set" else None
    mutation = _mutation("PAPER001", "review_note", value, base, operation=operation,
                         created_at="2026-10-08T12:00:00Z")
    calls = []

    def mirror(path, key):
        assert path == db and key == "PAPER001"
        assert _state(db)["sync_mutations"]
        with sqlite3.connect(db) as conn:
            desired = review_note_projection.snapshot(conn, key)
        metadata = {"note_written": False, "note_error": error, "note_pending": desired["pending"],
                    "projection_revision": desired["revision"], "target_key": desired["target_key"]}
        calls.append((metadata, desired["body"]))
        return metadata

    monkeypatch.setattr(service.verdict_effects, "mirror_review_note", mirror)
    response = _push(http, [mutation])
    assert response.status_code == 200, response.text
    first = response.json()["results"][0]
    assert first["status"] == "applied"
    assert first["review_note_mirror"] == calls[-1][0]
    assert first["review_note_mirror"] == {"note_written": False, "note_error": error,
        "note_pending": True, "projection_revision": first["applied_revision"], "target_key": "PAPER001"}
    assert "question_mirror" not in first
    assert repositories.get_review_note(db, "PAPER001") == value
    saved = _state(db)
    retry = _push(http, [mutation])
    assert retry.status_code == 200, retry.text
    assert retry.json()["results"][0] == {**first, "status": "already_applied"}
    assert _state(db) == saved

    repositories.upsert_review_note(db, "PAPER001", "newer current intent")
    current = _state(db)
    replay = _push(http, [mutation])
    assert replay.status_code == 200, replay.text
    result = replay.json()["results"][0]
    assert result["status"] == "already_applied"
    assert result["applied_revision"] == first["applied_revision"]
    assert result["review_note_mirror"] == calls[-1][0]
    assert result["review_note_mirror"]["projection_revision"] > first["applied_revision"]
    assert result["review_note_mirror"]["note_error"] == error
    assert result["review_note_mirror"]["note_pending"] is True
    assert calls[-1][1] == "newer current intent"
    assert _state(db) == current
    assert current["sync_mutations"] == saved["sync_mutations"]
    stored_request, stored_result = current["sync_mutations"][0][7:9]
    assert json.loads(stored_request) == mutation
    assert "review_note_mirror" not in json.loads(stored_result)
