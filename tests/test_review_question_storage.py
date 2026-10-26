"""Storage contract for immutable review-question submissions."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from zotero_summarizer.storage import review_questions
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


def _database(tmp_path):
    path = tmp_path / "triage.sqlite3"
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)
    return path


def _payload(*, submission_id=None, item_key="PAPER001", question="Why?"):
    return {
        "submission_id": submission_id or str(uuid4()),
        "item_key": item_key,
        "question": question,
        "origin": "typed",
        "preset_id": None,
    }


def _save(path, payload):
    with sqlite3.connect(path, timeout=10) as conn:
        return review_questions.save(conn, payload)


@pytest.mark.parametrize("item_key", ["", " \t\n", ".", "..", "../bad", "a/b", "a\\b", "a\0b", "/absolute", None, 42])
def test_storage_rejects_invalid_key_before_any_sql(tmp_path, item_key):
    path = _database(tmp_path)
    statements = []
    with sqlite3.connect(path) as conn:
        conn.set_trace_callback(statements.append)
        with pytest.raises(ValueError):
            review_questions.save(conn, _payload(item_key=item_key))
        conn.set_trace_callback(None)
        assert conn.execute("SELECT COUNT(*) FROM review_questions").fetchone()[0] == 0
    assert statements == []


@pytest.mark.parametrize("item_key", ["PAPER001", "feed:42", "feed:g:" + "a" * 64, "note:ABC12345:42", " PAPER001 "])
def test_valid_key_identity_and_exact_retry_are_preserved(tmp_path, item_key):
    path = _database(tmp_path)
    payload = _payload(item_key=item_key)

    saved, created = _save(path, payload)
    retry, retry_created = _save(path, payload)

    assert created is True and retry_created is False
    assert saved["item_key"] == item_key
    assert retry == saved
    with sqlite3.connect(path) as conn:
        assert review_questions.list_for_paper(conn, item_key) == [saved]


def test_question_survives_connection_reload(tmp_path):
    path = _database(tmp_path)
    payload = _payload()

    saved, created = _save(path, payload)
    with sqlite3.connect(path) as conn:
        reloaded = review_questions.list_for_paper(conn, payload["item_key"])

    assert created is True
    assert saved["submission_id"] == payload["submission_id"]
    assert saved["item_key"] == payload["item_key"]
    assert saved["question"] == payload["question"]
    assert saved["origin"] == "typed"
    assert saved["preset_id"] is None
    assert saved["submitted_at"]
    assert reloaded == [saved]


def test_preset_origin_and_id_are_preserved(tmp_path):
    path = _database(tmp_path)
    payload = {
        **_payload(question="Does the paper report validation?"),
        "origin": "preset",
        "preset_id": "external_validation",
    }

    saved, created = _save(path, payload)

    assert created is True
    assert saved["origin"] == "preset"
    assert saved["preset_id"] == "external_validation"


def test_concurrent_same_submission_id_creates_one_row(tmp_path):
    path = _database(tmp_path)
    payload = _payload()
    start = Barrier(2)

    def submit():
        with sqlite3.connect(path, timeout=10) as conn:
            start.wait()
            return review_questions.save(conn, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))

    assert sorted(created for _row, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM review_questions WHERE submission_id = ?",
            (payload["submission_id"],),
        ).fetchone()[0] == 1


def test_exact_retry_reuses_row_and_changed_payload_conflicts(tmp_path):
    path = _database(tmp_path)
    payload = _payload()

    first, first_created = _save(path, payload)
    retry, retry_created = _save(path, payload)

    assert first_created is True
    assert retry_created is False
    assert retry == first
    with pytest.raises(ValueError, match="submission_id"):
        _save(path, {**payload, "question": "A changed question?"})


def test_identical_questions_with_different_ids_are_intentional_repeats(tmp_path):
    path = _database(tmp_path)
    first = _payload(question="Same question")
    second = _payload(question="Same question")

    first_row, first_created = _save(path, first)
    second_row, second_created = _save(path, second)

    assert first_created is True
    assert second_created is True
    assert first_row["question"] == second_row["question"]
    assert first_row["submission_id"] != second_row["submission_id"]
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM review_questions WHERE question = ?", ("Same question",)
        ).fetchone()[0] == 2


def test_list_resolves_note_aliases_without_rewriting_original_item_key(tmp_path):
    path = _database(tmp_path)
    payload = _payload(item_key="feed:42", question="Alias question")
    stable_key = "feed:g:" + "a" * 64
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO feed_key_aliases(old_key, stable_feed_key) VALUES (?, ?)",
            ("feed:42", stable_key),
        )
        conn.execute(
            "INSERT INTO processed_feed_items(feed_library_id, feed_item_id, stable_feed_key, "
            "materialized_zotero_key, title, guid, decision, run_id) "
            "VALUES (1, 42, ?, 'PAPER001', 'Paper', 'guid', 'selected', 'test')",
            (stable_key,),
        )
        review_questions.save(conn, payload)

        rows = review_questions.list_for_paper(conn, "PAPER001")
        stored_key = conn.execute(
            "SELECT item_key FROM review_questions WHERE submission_id = ?",
            (payload["submission_id"],),
        ).fetchone()[0]

    assert [row["submission_id"] for row in rows] == [payload["submission_id"]]
    assert stored_key == "feed:42"
    assert rows[0]["item_key"] == "feed:42"
    retry, created = _save(path, payload)
    assert created is False
    assert retry == rows[0]
    with pytest.raises(ValueError, match="submission_id"):
        _save(path, {**payload, "item_key": "PAPER001"})


def test_question_rows_reject_in_place_mutation(tmp_path):
    path = _database(tmp_path)
    payload = _payload(question="Original")
    _save(path, payload)

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE review_questions SET question = 'Edited' WHERE submission_id = ?",
                (payload["submission_id"],),
            )


def test_saving_question_does_not_change_manual_note(tmp_path):
    path = _database(tmp_path)
    payload = _payload(question="Does the note stay?")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO review_notes(item_key, note, updated_at) VALUES (?, ?, ?)",
            (payload["item_key"], "Handwritten note", "2026-10-08T12:00:00Z"),
        )
        review_questions.save(conn, payload)
        note = conn.execute(
            "SELECT note FROM review_notes WHERE item_key = ?", (payload["item_key"],)
        ).fetchone()[0]

    assert note == "Handwritten note"
