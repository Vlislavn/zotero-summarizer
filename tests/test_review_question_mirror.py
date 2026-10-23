"""Composite delivery uses current committed intent and explicit acknowledgement."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests._composite_note_fixtures import CompositeNotes
from tests.test_daily_actions import _record
from tests.test_review_questions import _app, _database
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.library import review_questions, review_materialize, _review_cache
from zotero_summarizer.services.triage import daily_actions, feeds
from zotero_summarizer.services.triage.feeds import _daily_materialize
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.services.zotero.pending import USER_NOTE_MARKER, build_user_note_html
from zotero_summarizer.storage import repositories, review_note_projection, review_notes


@pytest.fixture
def mirror_env(tmp_path, monkeypatch):
    config = _database(tmp_path)
    resources = CompositeNotes(config, tmp_path / "owned-native", monkeypatch)
    return config.triage_db_path, resources.writes, resources


def _payload(key="P1", text="Question <one>?"):
    return {"submission_id": str(uuid4()), "item_key": key, "question": text, "origin": "typed"}


def _snapshot(path, key="P1"):
    with closing(sqlite3.connect(path)) as conn:
        return review_note_projection.snapshot(conn, key)


def _feed(path, target=None):
    pk = _record(path, 42)
    with closing(sqlite3.connect(path)) as conn, conn:
        row = conn.execute("SELECT stable_feed_key FROM processed_feed_items WHERE id=?", (pk,)).fetchone()
        stable = row[0]
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key=? WHERE id=?", (target, pk))
        conn.execute("INSERT OR REPLACE INTO feed_key_aliases(old_key, stable_feed_key) VALUES (?, ?)",
                     ("feed:42", stable))
    return pk, stable


@pytest.mark.parametrize("qa_error", [False, True])
def test_http_receipts_separate_committed_question_mirror_and_qa(mirror_env, monkeypatch, qa_error):
    path, writes, resources = mirror_env
    payload = _payload()
    repositories.upsert_review_note(path, "P1", "Manual & note")

    def upsert(key, body):
        with closing(sqlite3.connect(path, timeout=0)) as conn:
            assert conn.execute("SELECT submission_id FROM review_questions").fetchone()[0] == payload["submission_id"]
        raise ZoteroWriteError("synthetic connector failure", expected_io=True)

    def qa(*args, **kwargs):
        with closing(sqlite3.connect(path, timeout=0)) as conn:
            conn.execute("BEGIN IMMEDIATE")
        if qa_error:
            raise APIError("needs_pdf", "No PDF", 404, details={"source": "paper-reader"})
        return {"answer": None, "abstained": True, "citation": {"claimed": False}}

    monkeypatch.setattr(resources, "dispatch_hook", upsert)
    monkeypatch.setattr("zotero_summarizer.api.routes.library.qa.ask_paper", qa)
    with TestClient(_app()) as client:
        response = client.post("/api/library/ask", json=payload)
        saved = client.post("/api/library/questions", json=payload)

    assert response.status_code == (404 if qa_error else 200), response.text
    receipt = response.json()["details"] if qa_error else response.json()
    assert receipt["question_saved"] is True
    assert receipt["question_submission"]["submission_id"] == payload["submission_id"]
    assert receipt["question_mirror"]["note_written"] is False
    assert receipt["question_mirror"]["note_pending"] is True
    assert receipt["question_mirror"]["note_error"]
    assert saved.status_code == 200
    assert saved.json()["question_submission"] == receipt["question_submission"]
    assert writes == [("P1", "Manual & note\n\nSaved questions:\n1. Question <one>?")] * 2
    resources.assert_attempts([("P1", "Manual & note\n\nSaved questions:\n1. Question <one>?")] * 2)
    assert _snapshot(path)["pending"] is True
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_questions").fetchone()[0] == 1


def test_comment_only_retry_delivers_current_not_original_projection(mirror_env):
    path, writes, resources = mirror_env
    payload = _payload()
    with TestClient(_app()) as client:
        first = client.post("/api/library/questions", json=payload)
        repositories.upsert_review_note(path, "P1", "New manual note")
        second = client.post("/api/library/questions", json=payload)
        third = client.post("/api/library/questions", json=payload)

    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["question_mirror"]["note_written"] is True
    assert second.json()["question_mirror"]["note_pending"] is False
    assert third.json()["question_mirror"]["note_written"] is False
    assert third.json()["question_mirror"]["note_pending"] is False
    assert second.json()["question_submission"] == first.json()["question_submission"]
    assert writes == [("P1", "Saved questions:\n1. Question <one>?"),
                      ("P1", "New manual note\n\nSaved questions:\n1. Question <one>?")]
    resources.assert_attempts([("P1", "Saved questions:\n1. Question <one>?"),
                               ("P1", "New manual note\n\nSaved questions:\n1. Question <one>?")])


@pytest.mark.parametrize("manual", ["Stale manual body", "", None])
def test_alias_union_and_manual_save_clear_keep_questions(mirror_env, manual):
    path, writes, resources = mirror_env
    _, stable = _feed(path, "P1")
    for key, text in [("feed:42", "Legacy?"), (stable, "Stable?"), ("P1", "Library?")]:
        review_questions.save_question(_payload(key, text))
    repositories.upsert_review_note(path, stable, "Older manual body")
    if manual is None:
        with closing(sqlite3.connect(path)) as conn, conn:
            review_notes.write(conn, "P1", None)
    else:
        repositories.upsert_review_note(path, "P1", manual)

    result = verdict_effects.mirror_review_note(path, "feed:42")

    assert result["note_written"] is True and result["note_pending"] is False
    assert result["target_key"] == "P1"
    questions = review_questions.list_questions(stable)
    expected_questions = "Saved questions:\n" + "\n".join(
        f"{index}. {row['question']}" for index, row in enumerate(questions, 1)
    )
    assert writes == [("P1", (manual + "\n\n" if manual else "") + expected_questions)]
    resources.assert_attempts([("P1", (manual + "\n\n" if manual else "") + expected_questions)])
    assert {row["item_key"] for row in questions} == {"feed:42", stable, "P1"}
    assert _snapshot(path, "P1")["pending"] is False
    assert _snapshot(path, stable)["pending"] is False
    assert USER_NOTE_MARKER in build_user_note_html(writes[0][1])
    assert "&lt;" in build_user_note_html("<script>&</script>")


@pytest.mark.parametrize("key", ["feed:999", "note:P1:5", "P1"])
def test_absent_or_unavailable_target_remains_pending_without_upsert(mirror_env, key):
    path, writes, resources = mirror_env
    resources.make_unavailable()
    review_questions.save_question(_payload(key))

    result = verdict_effects.mirror_review_note(path, key)

    assert result["note_written"] is False and result["note_pending"] is True
    assert result["note_error"]
    assert writes == [] and _snapshot(path, key)["pending"] is True
    resources.assert_quiet()


def test_nonunique_feed_target_is_not_delivered(mirror_env):
    path, writes, resources = mirror_env
    _, stable = _feed(path, "P1")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO processed_feed_items(feed_library_id, feed_item_id, stable_feed_key, "
                     "title, guid, decision, run_id, materialized_zotero_key) "
                     "VALUES (2, 42, ?, 'Paper', 'g', 'selected', 'r', 'P2')", (stable,))
    review_questions.save_question(_payload(stable))

    result = verdict_effects.mirror_review_note(path, stable)

    assert result["target_key"] is None and result["note_pending"] is True
    assert result["note_written"] is False and writes == []
    resources.assert_quiet()


def test_unconfigured_connector_and_ack_failure_remain_retryable(mirror_env, monkeypatch):
    path, writes, resources = mirror_env
    review_questions.save_question(_payload())

    def unavailable():
        raise APIError("zotero_unavailable", "not configured", 503)

    with monkeypatch.context() as patch:
        patch.setattr(zotero, "get_zotero_reader_or_raise", unavailable)
        receipt = verdict_effects.mirror_review_note(path, "P1")
        assert receipt["note_written"] is False and receipt["note_pending"] is True
        assert receipt["note_error"] and writes == []
        resources.assert_quiet()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TRIGGER fail_ack BEFORE UPDATE OF acknowledged ON review_note_mirror_intents "
                     "WHEN NEW.acknowledged=1 BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END")

    failed = verdict_effects.mirror_review_note(path, "P1")

    assert failed["note_written"] is True and failed["note_pending"] is True
    assert failed["note_error"] and _snapshot(path)["pending"] is True
    before_retry = resources.checkpoint()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("DROP TRIGGER fail_ack")
    retried = verdict_effects.mirror_review_note(path, "P1")
    assert retried["note_pending"] is False and retried["note_error"] is None
    assert retried["note_written"] is False
    assert writes == [("P1", "Saved questions:\n1. Question <one>?")]
    resources.assert_ack_retry(before_retry)
    resources.assert_attempts([("P1", "Saved questions:\n1. Question <one>?")])


def test_busy_snapshot_admission_returns_five_unknown_fields_without_delivery(mirror_env, monkeypatch):
    path, writes, resources = mirror_env
    original = review_questions.save_question(_payload())
    real_connect = sqlite3.connect
    monkeypatch.setattr(repositories, "_connect_to", lambda db_path: real_connect(db_path, timeout=0))
    monkeypatch.setattr(review_note_projection, "acknowledge", lambda *_args: pytest.fail("unavailable snapshot acknowledged"))
    with closing(real_connect(path, timeout=0)) as locked:
        locked.execute("BEGIN IMMEDIATE")

        receipt = verdict_effects.mirror_review_note(path, "P1")

    assert receipt == {"note_written": False, "note_error": "OperationalError", "note_pending": None,
                       "projection_revision": None, "target_key": None}
    assert writes == [] and _snapshot(path)["pending"] is True
    resources.assert_quiet()
    with closing(real_connect(path)) as conn:
        assert conn.execute("SELECT submission_id, question FROM review_questions").fetchall() == [
            (original["submission_id"], original["question"]),
        ]


@pytest.mark.parametrize("defect", ["programming", "runtime", "schema"])
def test_programming_and_schema_errors_do_not_become_success(mirror_env, monkeypatch, defect):
    path, _, resources = mirror_env
    review_questions.save_question(_payload())
    if defect != "schema":
        error = ValueError if defect == "programming" else RuntimeError

        def invalid(*args):
            raise error("invalid writer state")
        monkeypatch.setattr(resources, "entry_hook", invalid)
    else:
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("DROP TABLE review_note_mirror_intents")
        error = sqlite3.OperationalError

    with pytest.raises(error):
        verdict_effects.mirror_review_note(path, "P1")
    if defect == "schema":
        resources.assert_quiet()
    else:
        resources.assert_attempts([("P1", "Saved questions:\n1. Question <one>?")])


def test_delayed_delivery_serializes_and_cannot_acknowledge_newer_question(mirror_env, monkeypatch):
    path, writes, resources = mirror_env
    old_submission = review_questions.save_question(_payload(text="Old?"))
    old_desired = _snapshot(path)
    entered, release = Event(), Event()

    def paused(key, body):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(resources, "dispatch_hook", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(verdict_effects.mirror_review_note, path, "P1")
        try:
            assert entered.wait(5)
            saving = pool.submit(review_questions.save_question, _payload(text="New?"))
            with pytest.raises(TimeoutError):
                saving.result(timeout=0.05)
        finally:
            release.set()
        delivered = delivery.result(timeout=5)
        assert delivered["note_pending"] is False
        assert delivered["projection_revision"] == old_desired["revision"]
        assert delivered["target_key"] == old_desired["target_key"]
        new_submission = saving.result(timeout=5)
    newer = _snapshot(path)
    assert newer["pending"] is True and newer["revision"] > delivered["projection_revision"]
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("SELECT submission_id, question FROM review_questions "
                            "WHERE item_key='P1' ORDER BY submitted_at, submission_id").fetchall()
    assert len(rows) == 2
    assert {row[0]: row[1] for row in rows} == {
        old_submission["submission_id"]: "Old?", new_submission["submission_id"]: "New?",
    }
    assert old_submission["submission_id"] != new_submission["submission_id"]
    expected = "Saved questions:\n" + "\n".join(f"{index}. {row[1]}" for index, row in enumerate(rows, 1))
    verdict_effects.mirror_review_note(path, "P1")
    assert writes == [("P1", "Saved questions:\n1. Old?"), ("P1", expected)]
    resources.assert_attempts([("P1", "Saved questions:\n1. Old?"), ("P1", expected)])
    assert _snapshot(path)["pending"] is False


@pytest.mark.parametrize("same_timestamp", [True, False])
def test_projection_order_uses_timestamp_then_uuid_not_insertion(mirror_env, same_timestamp):
    path, writes, resources = mirror_env
    timestamp = "2026-10-08T00:00:00Z"
    new_timestamp = timestamp if same_timestamp else "2026-10-08T00:00:01Z"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executemany("INSERT INTO review_questions(submission_id, item_key, question, origin, submitted_at) "
                         "VALUES (?, 'P1', ?, 'typed', ?)", [
            ("ffffffff-ffff-4fff-8fff-ffffffffffff", "Old?", timestamp),
            ("00000000-0000-4000-8000-000000000001", "New?", new_timestamp),
        ])
    expected = "Saved questions:\n1. New?\n2. Old?" if same_timestamp else "Saved questions:\n1. Old?\n2. New?"

    receipt = verdict_effects.mirror_review_note(path, "P1")

    assert writes == [("P1", expected)]
    resources.assert_attempts([("P1", expected)])
    assert receipt["note_written"] is True and receipt["note_pending"] is False
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*), COUNT(DISTINCT submission_id) FROM review_questions").fetchone() == (2, 2)


@pytest.fixture
def materialize_env(mirror_env, monkeypatch):
    path, writes, resources = mirror_env
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: {"digest": {"tldr": "Fixture digest"}})
    monkeypatch.setattr(feeds, "_feed_payload_from_row", lambda row: {"title": row["title"]})
    monkeypatch.setattr(_daily_materialize, "_feed_payload_from_row", lambda row: {"title": row["title"]})
    monkeypatch.setattr(daily_actions, "_record_label", lambda *args, **kwargs: None)
    monkeypatch.setattr(daily_actions.deep_review, "copy_review", lambda *args: None)
    monkeypatch.setattr(daily_actions, "_attach_fulltext_best_effort", lambda pairs: {"attached": 0})
    monkeypatch.setattr(daily_actions, "_carry_renders_best_effort", lambda pairs: None)
    return path, writes, resources.creations, resources.writer


@pytest.mark.parametrize("flow", ["add", "verdict", "recovery", "legacy"])
def test_materialization_and_recovery_deliver_questions_after_original_commit(materialize_env, flow, mirror_env):
    path, writes, creations, writer = materialize_env
    resources = mirror_env[2]
    pk, stable = _feed(path)
    original = review_questions.save_question(_payload("feed:42"))
    assert writes == []
    if flow in {"recovery", "legacy"}:
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE processed_feed_items SET decision='user_approved' WHERE id=?", (pk,))
    if flow == "add":
        assert daily_actions.add_to_library([pk])["added"] == 1
    elif flow == "verdict":
        assert daily_actions.materialize_feed_verdict(stable, "must_read")["added"] is True
    elif flow == "recovery":
        assert review_materialize.apply_all_approved()["applied"] == 1
    else:
        row = daily_actions._load_feed_row(stable)
        pick = _daily_materialize._PendingScoredRow(2.0, 0.0, False, row, stable)
        ctx = _daily_materialize._MaterializeCtx("Inbox", "", 7, "approved")
        _daily_materialize.materialize_pick(pick, writer=writer, run_id="r", used_keys=set(), ctx=ctx)

    assert len(creations) == 1
    assert writes == [(creations[0], "Saved questions:\n1. Question <one>?")]
    assert _snapshot(path, stable)["pending"] is False
    assert review_questions.list_questions(stable) == [original]
    repositories.upsert_review_note(path, stable, "Later manual")
    assert daily_actions.add_to_library([pk])["added"] == 0
    assert writes[-1] == (creations[0], "Later manual\n\nSaved questions:\n1. Question <one>?")
    assert len(creations) == 1
    resources.assert_attempts([(creations[0], "Saved questions:\n1. Question <one>?"),
                               (creations[0], "Later manual\n\nSaved questions:\n1. Question <one>?")])
    resources.assert_created(creations[0])
    assert (resources.materializations[0]["writer"] is writer) is (flow == "legacy")


@pytest.mark.parametrize("error", [ZoteroWriteError, ValueError, sqlite3.OperationalError])
@pytest.mark.parametrize("flow", ["add", "verdict", "recovery"])
def test_note_failure_after_materialization_never_becomes_item_pending(materialize_env, monkeypatch, error, flow, mirror_env):
    path, writes, creations, _ = materialize_env
    resources = mirror_env[2]
    pk, stable = _feed(path)
    review_questions.save_question(_payload(stable))
    if flow == "recovery":
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE processed_feed_items SET decision='user_approved' WHERE id=?", (pk,))

    def fail(key, body):
        if error is ZoteroWriteError:
            raise error("synthetic note failure", expected_io=True)
        raise error("synthetic note failure")

    monkeypatch.setattr(resources, "dispatch_hook" if error is ZoteroWriteError else "entry_hook", fail)
    if error is not ZoteroWriteError and flow != "add":
        with pytest.raises(error, match="synthetic note failure"):
            if flow == "verdict":
                daily_actions.materialize_feed_verdict(stable, "must_read")
            else:
                review_materialize.apply_all_approved()
    else:
        if flow == "add":
            result = daily_actions.add_to_library([pk])
            assert result["added"] == 1 and result["pending_sync"] == 0
            if error is not ZoteroWriteError:
                assert result["failed"][0]["code"] == "note_delivery_failed"
            else:
                assert result["note_mirrors"][0]["note_pending"] is True
                assert result["note_mirrors"][0]["note_error"] == "ZoteroWriteError"
        elif flow == "verdict":
            result = daily_actions.materialize_feed_verdict(stable, "must_read")
            assert result["added"] is True and result["status"] == "added"
            assert result["review_note_mirror"]["note_pending"] is True
        else:
            result = review_materialize.apply_all_approved()
            assert result["applied"] == 1 and result["pending_sync"] == 0
            assert result["note_mirrors"][0]["note_pending"] is True
    assert len(creations) == len(writes) == 1
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT decision, materialized_zotero_key, zotero_sync_status "
                           "FROM processed_feed_items WHERE id=?", (pk,)).fetchone()
    assert row[0] == "selected" and row[1] == creations[0] and row[2] == "synced"
    assert _snapshot(path, stable)["pending"] is True
    resources.assert_attempts([(creations[0], "Saved questions:\n1. Question <one>?")])
    resources.assert_created(creations[0])


@pytest.mark.parametrize("flow", ["verdict", "row", "legacy"])
def test_existing_target_and_target_change_retry_current_projection(materialize_env, flow, mirror_env):
    path, writes, creations, writer = materialize_env
    resources = mirror_env[2]
    pk, stable = _feed(path, "P1")
    review_questions.save_question(_payload(stable))
    local_before = resources.local()
    verdict_effects.mirror_review_note(path, stable)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key='P2', decision='user_approved' WHERE id=?", (pk,))
    if flow == "verdict":
        assert daily_actions.materialize_feed_verdict(stable, "must_read")["status"] == "already_in_library"
    elif flow == "row":
        assert review_materialize.materialize_row(daily_actions._load_feed_row(stable), writer=writer, used_keys=set()) == "P2"
    else:
        row = daily_actions._load_feed_row(stable)
        pick = _daily_materialize._PendingScoredRow(2.0, 0.0, False, row, stable)
        ctx = _daily_materialize._MaterializeCtx("Inbox", "", 7, "approved")
        assert _daily_materialize.materialize_pick(pick, writer=writer, run_id="r", used_keys=set(), ctx=ctx) == "P2"
    assert creations == []
    assert writes == [("P1", "Saved questions:\n1. Question <one>?"), ("P2", "Saved questions:\n1. Question <one>?")]
    assert _snapshot(path, stable)["pending"] is False
    resources.assert_parent_change([("P1", "Saved questions:\n1. Question <one>?"),
                                    ("P2", "Saved questions:\n1. Question <one>?")], local_before)
