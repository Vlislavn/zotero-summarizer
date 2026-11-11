"""Persistence boundary for composite manual notes and immutable questions."""

import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from uuid import uuid4

import pytest

from zotero_summarizer.storage import repositories, review_notes, review_questions
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations

STABLE = "feed:g:" + "a" * 64


def _projection():
    return importlib.import_module("zotero_summarizer.storage.review_note_projection")


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "triage.sqlite3"
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)
    return path


def _payload(key="PAPER001", question="Why?", **extra):
    return dict(submission_id=str(uuid4()), item_key=key, question=question,
                origin="typed", preset_id=None, **extra)


def _save(path, payload):
    with closing(repositories._connect_to(path)) as conn, conn:
        return review_questions.save(conn, payload)


def _snapshot(path, key="PAPER001"):
    with closing(repositories._connect_to(path)) as conn:
        return _projection().snapshot(conn, key)


def _feed(path, *, target=None):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
            "title, guid, decision, run_id, materialized_zotero_key) "
            "VALUES (1, 42, ?, 'Paper', 'guid', 'selected', 'test', ?)", (STABLE, target),
        )
        conn.execute("INSERT INTO feed_key_aliases(old_key, stable_feed_key) VALUES ('feed:7', ?)", (STABLE,))


def _stored_state(conn):
    return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("review_questions", "review_notes", "sync_changes", "review_note_mirror_intents")}


def test_question_commit_advances_cursor_and_exact_replay_stages_nothing(path):
    payload = _payload(question="  Original body\n😀?  ")
    before = repositories.sync_status(path)["cursor"]
    saved, created = _save(path, payload)

    after = repositories.pull_sync_changes(path, before)["cursor"]
    assert created and after > before
    with closing(repositories._connect_to(path)) as conn:
        journal = conn.execute("SELECT item_key, field, value FROM sync_changes WHERE revision = ?", (after,)).fetchone()
        assert tuple(journal) == (payload["item_key"], "review_question", saved["submission_id"])
        assert conn.execute("SELECT COUNT(*) FROM review_note_mirror_intents").fetchone()[0] == 1
        original = _stored_state(conn)
    assert _save(path, payload) == (saved, False)
    with closing(repositories._connect_to(path)) as conn:
        assert _stored_state(conn) == original
    desired = _snapshot(path)
    assert desired == {"item_key": "PAPER001", "revision": after,
                       "body": "Saved questions:\n1.   Original body\n😀?  ",
                       "target_key": "PAPER001", "pending": True}


def test_independent_concurrent_questions_get_ordered_revisions(path):
    start = Barrier(2)
    payloads = [_payload(question="Same deliberate repeat") for _ in range(2)]

    def submit(payload):
        start.wait()
        return _save(path, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, payloads))
    assert all(created for _, created in results)
    with closing(repositories._connect_to(path)) as conn:
        changes = conn.execute("SELECT revision, value FROM sync_changes WHERE field = 'review_question' ORDER BY revision").fetchall()
        assert len(changes) == 2 and changes[0][0] < changes[1][0]
        assert {row[1] for row in changes} == {p["submission_id"] for p in payloads}
        rows = review_questions.list_for_paper(conn, "PAPER001")
    assert _snapshot(path)["body"] == "Saved questions:\n" + "\n".join(
        f"{i}. {row['question']}" for i, row in enumerate(rows, 1))
    assert _snapshot(path)["revision"] == changes[-1][0]


@pytest.mark.parametrize("failure", ["caller", "staging"])
def test_question_and_manual_write_rollback_row_journal_and_pending_together(path, failure):
    _save(path, _payload(question="Kept"))
    repositories.upsert_review_note(path, "PAPER001", "Kept manual")
    with closing(repositories._connect_to(path)) as conn:
        before = _stored_state(conn)
        if failure == "staging":
            conn.execute("CREATE TRIGGER reject_intent BEFORE UPDATE ON review_note_mirror_intents "
                         "BEGIN SELECT RAISE(ABORT, 'injected staging failure'); END")
            conn.commit()
        with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
            with conn:
                review_questions.save(conn, _payload(question="Rolled back"))
                review_notes.write(conn, "PAPER001", "Rolled back manual")
                raise RuntimeError("caller failure")
        assert _stored_state(conn) == before


@pytest.mark.parametrize("clear", ["", None])
def test_stale_manual_save_and_clear_never_remove_questions(path, clear):
    repositories.upsert_review_note(path, "PAPER001", "Manual before question")
    _save(path, _payload(question="Saved separately"))
    repositories.upsert_review_note(path, "PAPER001", "Stale manual replacement")
    assert _snapshot(path)["body"] == "Stale manual replacement\n\nSaved questions:\n1. Saved separately"
    with closing(repositories._connect_to(path)) as conn, conn:
        review_notes.write(conn, "PAPER001", clear)
    assert _snapshot(path)["body"] == "Saved questions:\n1. Saved separately"
    with closing(repositories._connect_to(path)) as conn:
        assert review_questions.list_for_paper(conn, "PAPER001")[0]["question"] == "Saved separately"
        assert review_notes.current(conn, "PAPER001")["value"] == clear


def test_manual_staging_failure_rolls_back_note_and_its_revision(path):
    repositories.upsert_review_note(path, "PAPER001", "Kept")
    with closing(repositories._connect_to(path)) as conn, conn:
        before = _stored_state(conn)
        conn.execute("CREATE TRIGGER reject_manual_intent BEFORE UPDATE ON review_note_mirror_intents "
                     "BEGIN SELECT RAISE(ABORT, 'manual staging failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="manual staging failure"):
        repositories.upsert_review_note(path, "PAPER001", "Rejected")
    with closing(repositories._connect_to(path)) as conn:
        assert _stored_state(conn) == before


def test_online_and_offline_manual_writers_stage_in_their_transaction(path):
    repositories.upsert_review_note(path, "PAPER001", "Online")
    online = _snapshot(path)
    assert online["pending"] and online["body"] == "Online"
    request = dict(mutation_id=str(uuid4()), device_id="test", item_key="PAPER001", field="review_note",
                   operation="delete", value=None, base_revision=online["revision"], created_at="2026-10-08")
    result = repositories.apply_sync_mutation(path, request)
    desired = _snapshot(path)
    assert desired["pending"] and desired["body"] == ""
    assert desired["revision"] == result["applied_revision"]
    with closing(repositories._connect_to(path)) as conn:
        before = _stored_state(conn)
    assert repositories.apply_sync_mutation(path, request)["status"] == "already_applied"
    with closing(repositories._connect_to(path)) as conn:
        assert _stored_state(conn) == before


def test_alias_union_restart_keeps_original_question_keys_and_composite_ack(path):
    _feed(path)
    old = _payload("feed:7", "Feed question")
    library = _payload("PAPER001", "Library question")
    old_row, _ = _save(path, old)
    library_row, _ = _save(path, library)
    repositories.upsert_review_note(path, "PAPER001", "Manual library")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'PAPER001'")
    projection = _projection()
    desired = _snapshot(path, STABLE)
    for key in ("feed:7", "feed:42", STABLE, "PAPER001"):
        assert {k: v for k, v in _snapshot(path, key).items() if k != "item_key"} == {
            k: v for k, v in desired.items() if k != "item_key"}
    with projection.current_for_mirror(path, "feed:7") as (conn, current):
        assert projection.acknowledge(conn, current)
    assert not _snapshot(path, "PAPER001")["pending"]
    assert _save(path, old) == (old_row, False)
    assert _save(path, library) == (library_row, False)
    with closing(repositories._connect_to(path)) as conn:
        assert {row["item_key"] for row in review_questions.list_for_paper(conn, STABLE)} == {"feed:7", "PAPER001"}


def test_alias_union_invalidates_subset_ack_even_when_revision_is_unchanged(path):
    _feed(path)
    _save(path, _payload(STABLE, "Earlier feed"))
    latest, _ = _save(path, _payload("PAPER001", "Later library"))
    projection = _projection()
    with projection.current_for_mirror(path, "PAPER001") as (conn, subset):
        assert projection.acknowledge(conn, subset)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'PAPER001'")
    union = _snapshot(path)
    assert union["revision"] == subset["revision"]
    assert union["body"] != subset["body"] and union["pending"]
    with projection.current_for_mirror(path, STABLE) as (conn, desired):
        assert not projection.acknowledge(conn, subset)
        assert projection.acknowledge(conn, desired)
    with closing(repositories._connect_to(path)) as conn:
        before = _stored_state(conn)
    assert _save(path, {k: latest[k] for k in latest if k != "submitted_at"}) == (latest, False)
    repositories.upsert_review_note(path, "PAPER001", "")
    with projection.current_for_mirror(path, STABLE) as (conn, clear):
        assert projection.acknowledge(conn, clear)
    same = _snapshot(path)
    repositories.upsert_review_note(path, STABLE, "")
    assert _snapshot(path) == same
    with closing(repositories._connect_to(path)) as conn:
        assert _stored_state(conn)["review_questions"] == before["review_questions"]


def test_feed_materialization_target_change_and_noop_recovery(path):
    _feed(path)
    _save(path, _payload(STABLE))
    projection = _projection()
    with projection.current_for_mirror(path, STABLE) as (conn, absent):
        assert absent["target_key"] is None and absent["pending"]
        assert not projection.acknowledge(conn, absent)
    assert _snapshot(path, STABLE)["pending"]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'PAPER001'")
    with projection.current_for_mirror(path, STABLE) as (conn, desired):
        assert desired["target_key"] == "PAPER001" and desired["pending"]
        assert not projection.acknowledge(conn, absent)
        assert projection.acknowledge(conn, desired)
    assert not _snapshot(path, STABLE)["pending"]
    with projection.current_for_mirror(path, STABLE) as (conn, noop):
        projection.stage(conn, STABLE)
        assert noop["pending"] is False
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'PAPER002'")
    changed = _snapshot(path, STABLE)
    assert changed["revision"] == desired["revision"] and changed["body"] == desired["body"]
    assert changed["target_key"] == "PAPER002" and changed["pending"]
    with projection.current_for_mirror(path, STABLE) as (conn, latest):
        assert not projection.acknowledge(conn, desired)
        assert projection.acknowledge(conn, latest)
    assert not _snapshot(path, STABLE)["pending"]


@pytest.mark.parametrize("exit_kind", ["without_ack", "exception", "ack_then_exception"])
def test_delivery_needs_explicit_ack_and_successful_transaction_exit(path, exit_kind):
    _save(path, _payload())
    projection = _projection()
    if exit_kind == "without_ack":
        with projection.current_for_mirror(path, "PAPER001"):
            pass
    else:
        with pytest.raises(RuntimeError, match="external failure"):
            with projection.current_for_mirror(path, "PAPER001") as (conn, desired):
                if exit_kind == "ack_then_exception":
                    assert projection.acknowledge(conn, desired)
                raise RuntimeError("external failure")
    assert _snapshot(path)["pending"]
    with projection.current_for_mirror(path, "PAPER001") as (conn, desired):
        assert projection.acknowledge(conn, desired)
    assert not _snapshot(path)["pending"]


@pytest.mark.parametrize("tamper", ["revision", "body", "target_key"])
def test_stale_or_tampered_acknowledgement_is_rejected(path, tamper):
    _save(path, _payload())
    projection = _projection()
    old = _snapshot(path)
    _save(path, _payload(question="Newer"))
    with projection.current_for_mirror(path, "PAPER001") as (conn, desired):
        assert not projection.acknowledge(conn, old)
        altered = {**desired, tamper: 0 if tamper == "revision" else "wrong"}
        assert not projection.acknowledge(conn, altered)
        assert projection.snapshot(conn, "PAPER001")["pending"]
        assert projection.acknowledge(conn, {**desired, "pending": False})
    assert not _snapshot(path)["pending"]


def test_delivery_lock_lifetime_and_unrelated_verdicts_do_not_make_note_edits(path):
    _save(path, _payload())
    projection = _projection()
    with projection.current_for_mirror(path, "PAPER001") as (conn, desired):
        with closing(sqlite3.connect(path, timeout=0)) as other:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        assert projection.acknowledge(conn, desired)
    repositories.insert_or_update_label_verdict(path, item_key="PAPER001", original_derived_priority="unknown",
                                              user_priority="should_read", comment="Unrelated")
    assert repositories.sync_status(path)["cursor"] > desired["revision"]
    after = _snapshot(path)
    assert after == {**desired, "pending": False}
    repositories.upsert_review_note(path, "PAPER001", "Later manual")
    assert _snapshot(path)["pending"]


def test_empty_projection_can_clear_owned_note_but_absence_is_not_delivery(path):
    projection = _projection()
    assert _snapshot(path)["pending"] is False
    with projection.current_for_mirror(path, "PAPER001") as (conn, empty):
        assert not projection.acknowledge(conn, empty)
    repositories.upsert_review_note(path, "PAPER001", "Owned")
    with projection.current_for_mirror(path, "PAPER001") as (conn, desired):
        assert projection.acknowledge(conn, desired)
    with closing(repositories._connect_to(path)) as conn, conn:
        review_notes.write(conn, "PAPER001", None)
    assert _snapshot(path)["body"] == "" and _snapshot(path)["pending"]
    with projection.current_for_mirror(path, "PAPER001") as (conn, clear):
        assert projection.acknowledge(conn, clear)
    assert not _snapshot(path)["pending"]


def test_ambiguous_feed_id_and_multi_library_family_do_not_guess_targets(path):
    _feed(path, target="PAPER001")
    other = "feed:g:" + "b" * 64
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                     "title, guid, decision, run_id, materialized_zotero_key) "
                     "VALUES (2, 42, ?, 'Other', 'other', 'selected', 'test', 'PAPER002')", (other,))
    _save(path, _payload("feed:42", "Ambiguous id"))
    assert _snapshot(path, "feed:42")["target_key"] is None
    assert _snapshot(path, STABLE)["body"] == ""
    assert _snapshot(path, other)["body"] == ""
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE processed_feed_items SET stable_feed_key=? WHERE feed_library_id=2", (STABLE,))
    _save(path, _payload(STABLE))
    with _projection().current_for_mirror(path, STABLE) as (conn, desired):
        assert desired["target_key"] is None and desired["pending"]
        assert not _projection().acknowledge(conn, desired)


@pytest.mark.parametrize("key", ["note:ABC12345:42", "feed:999"])
def test_non_library_identity_never_acknowledges_without_target(path, key):
    _save(path, _payload(key))
    with _projection().current_for_mirror(path, key) as (conn, desired):
        assert desired["target_key"] is None and desired["pending"]
        assert not _projection().acknowledge(conn, desired)


@pytest.mark.parametrize("length", [2000, 2001])
def test_storage_question_bound_matches_http_code_points_before_sql(path, length):
    payload = _payload(" K" + "x" * 3000 + " ", "😀" * length)
    payload.update(origin="preset", preset_id="preset" * 1000)
    with sqlite3.connect(path) as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        if length == 2001:
            with pytest.raises(ValueError, match="2000"):
                review_questions.save(conn, payload)
            assert statements == []
        else:
            row, created = review_questions.save(conn, payload)
            assert created and row["question"] == payload["question"]
            assert row["item_key"] == payload["item_key"] and row["preset_id"] == payload["preset_id"]


@pytest.mark.parametrize("lookup,target", [("PAPER001", "PAPER001"), (STABLE, None)],
                         ids=["unrelated-library", "feed-family"])
def test_empty_mirror_resolves_alias_family_once_per_snapshot(path, monkeypatch, lookup, target):
    from zotero_summarizer.services.golden import verdict_effects
    from zotero_summarizer.services.zotero import zotero

    _feed(path)
    calls = []

    def observe(binding, original):
        def resolver(conn, key):
            keys = original(conn, key)
            calls.append((binding, key, keys))
            return keys
        return resolver

    def no_delivery(*_args, **_kwargs):
        pytest.fail("Empty projection attempted external delivery")

    monkeypatch.setattr(review_notes, "resolve_item_key_aliases",
                        observe("review_notes", review_notes.resolve_item_key_aliases))
    monkeypatch.setattr(review_questions, "resolve_item_key_aliases",
                        observe("review_questions", review_questions.resolve_item_key_aliases))
    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", no_delivery)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", no_delivery)

    receipt = verdict_effects.mirror_review_note(path, lookup)

    assert receipt == {"note_written": False, "note_error": None, "note_pending": False,
                       "projection_revision": 0, "target_key": target}
    assert len(calls) == 2, calls
    family = sorted(("feed:7", "feed:42", STABLE)) if lookup == STABLE else [lookup]
    assert calls == [("review_notes", lookup, family), ("review_notes", lookup, family)]
