"""One local review note survives feed materialization and offline alias edits."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tests.test_golden_input_boundaries import _app
from tests.test_offline_sync import _db, _mutation
from zotero_summarizer.api.routes import golden
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import feeds, repositories as db

STABLE = "feed:g:" + "a" * 64


def _feed(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
            "title, guid, decision, run_id) VALUES (1, 42, ?, 'Paper', 'guid', 'selected', 'test')", (STABLE,),
        )


def _materialize(path):
    with sqlite3.connect(path) as conn:
        assert feeds.record_materialization(
            conn, feed_library_id=1, feed_item_id=42, materialized_zotero_key="PAPER001",
            outcome_window_days=7,
        )


@pytest.mark.parametrize("key", ["feed:42", STABLE])
def test_online_note_survives_materialization_and_edits_through_both_keys(tmp_path, monkeypatch, key):
    path = _db(tmp_path)
    _feed(path)
    monkeypatch.setattr(golden, "_db_path", lambda: path)
    monkeypatch.setattr(golden, "_load_all", lambda: [])
    monkeypatch.setattr(golden, "_build_source_payload", lambda key: {"title": "Paper"})
    monkeypatch.setattr(golden.verdict_effects, "mirror_review_note", lambda *args: {})
    with TestClient(_app()) as client:
        assert client.post("/api/golden/review-note", json={"item_key": key, "note": "Résumé"}).status_code == 200
        _materialize(path)
        detail = client.get("/api/golden/review-detail", params={"item_key": "PAPER001"})
        assert detail.status_code == 200
        assert detail.json()["user_note"] == "Résumé"
        for edited_key, note in [("PAPER001", "Résumé"), (key, "late feed edit"), ("PAPER001", "")]:
            assert client.post("/api/golden/review-note", json={"item_key": edited_key, "note": note}).status_code == 200
            for lookup in (key, "PAPER001", STABLE):
                response = client.get("/api/golden/review-detail", params={"item_key": lookup})
                assert response.status_code == 200, response.text
                assert response.json()["user_note"] == note
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_notes").fetchone()[0] == 1


def test_offline_alias_conflict_replay_delete_and_pull_share_one_revision(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _feed(path)
    db.upsert_review_note(path, "feed:42", "before adding")
    base = db.sync_current_fields(path)[("feed:42", "review_note")]["revision"]
    _materialize(path)
    monkeypatch.setattr(service.verdict_effects, "mirror_review_note", lambda *args: {})
    monkeypatch.setattr(service.reading_queue, "build_reading_queue", lambda **kw: {"items": [{"item_key": "PAPER001"}]})
    monkeypatch.setattr(service.deep_review, "_read_all", lambda: {})
    paper = next(p for p in service.pull(path, 0)["papers"] if p["item_key"] == "PAPER001")
    assert paper["review_note"] == "before adding"
    assert paper["revisions"]["review_note"] == base

    db.upsert_review_note(path, "PAPER001", "online edit")
    mutation = _mutation("feed:42", "review_note", "offline edit", base)
    conflict = service.push(path, [mutation])["results"][0]
    assert conflict["status"] == "conflict"
    assert conflict["canonical"] == {"value": "online edit"}
    assert service.push(path, [mutation])["results"][0] == conflict
    resolution = _mutation("feed:42", "review_note", "offline edit", conflict["conflict_revision"],
                           resolves_mutation_id=mutation["mutation_id"])
    applied = service.push(path, [resolution])["results"][0]
    assert applied["status"] == "applied"
    db.upsert_review_note(path, "PAPER001", "newer edit")
    assert service.push(path, [resolution])["results"][0]["status"] == "already_applied"
    assert db.get_review_note(path, STABLE) == "newer edit"
    revision = db.sync_current_fields(path)[(STABLE, "review_note")]["revision"]
    deleted = service.push(path, [_mutation("PAPER001", "review_note", None, revision, operation="delete")])["results"][0]
    assert deleted["status"] == "applied"
    for key in ("PAPER001", STABLE, "feed:42"):
        assert db.get_review_note(path, key) is None
        assert db.sync_current_fields(path)[(key, "review_note")]["revision"] == deleted["applied_revision"]


@pytest.mark.parametrize("latest", ["feed:42", "PAPER001"])
def test_existing_split_notes_use_latest_revision_and_do_not_resurrect(tmp_path, latest):
    path = _db(tmp_path)
    _feed(path)
    _materialize(path)
    older = "PAPER001" if latest == "feed:42" else "feed:42"
    with sqlite3.connect(path) as conn:
        for key, note in [(older, "old"), (latest, "new")]:
            conn.execute("INSERT INTO review_notes VALUES (?, ?, '2026-09-05')", (key, note))
    assert db.get_review_note(path, older) == "new"
    revision = db.sync_current_fields(path)[(older, "review_note")]["revision"]
    db.apply_sync_mutation(path, _mutation(older, "review_note", None, revision, operation="delete"))
    assert db.get_review_note(path, latest) is None
    assert db.get_review_note(path, older) is None
    db.upsert_review_note(path, older, "restored")
    assert db.get_review_note(path, latest) == "restored"


def test_ambiguous_legacy_id_does_not_merge_unrelated_notes(tmp_path):
    path = _db(tmp_path)
    _feed(path)
    _materialize(path)
    other = "feed:g:" + "b" * 64
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
            "title, guid, decision, run_id) VALUES (2, 42, ?, 'Other', 'other', 'selected', 'test')", (other,),
        )
    db.upsert_review_note(path, "feed:42", "ambiguous")
    assert db.get_review_note(path, "PAPER001") is None
    assert db.get_review_note(path, other) is None
    db.upsert_review_note(path, STABLE, "ours")
    assert db.get_review_note(path, "PAPER001") == "ours"
    assert db.get_review_note(path, "feed:42") == "ambiguous"


def test_historical_alias_and_presync_notes_keep_latest_timestamp(tmp_path):
    path = _db(tmp_path)
    _feed(path)
    _materialize(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO feed_key_aliases (old_key, stable_feed_key) VALUES ('feed:7', ?)", (STABLE,))
        conn.execute("INSERT INTO review_notes VALUES ('feed:7', 'new', '2026-09-05')")
        conn.execute("INSERT INTO review_notes VALUES ('PAPER001', 'old', '2026-09-04')")
        conn.execute("DELETE FROM sync_changes")
    assert db.get_review_note(path, "PAPER001") == "new"
    assert db.sync_current_fields(path)[("PAPER001", "review_note")]["revision"] == 0
    db.upsert_review_note(path, STABLE, "edited")
    assert db.get_review_note(path, "feed:7") == "edited"


def test_concurrent_offline_alias_edits_conflict_and_failed_update_rolls_back(tmp_path):
    path = _db(tmp_path)
    _feed(path)
    db.upsert_review_note(path, "feed:42", "original")
    _materialize(path)
    base = db.sync_current_fields(path)[("PAPER001", "review_note")]["revision"]
    mutations = [_mutation(key, "review_note", key, base) for key in ("feed:42", "PAPER001")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda mutation: db.apply_sync_mutation(path, mutation), mutations))
    assert sorted(result["status"] for result in results) == ["applied", "conflict"]
    winner = next(result for result in results if result["status"] == "applied")
    assert db.get_review_note(path, STABLE) == winner["canonical"]["value"]
    before = db.sync_status(path)["cursor"]
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER reject_note BEFORE UPDATE ON review_notes "
                     "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        db.upsert_review_note(path, STABLE, "must not persist")
    assert db.sync_status(path)["cursor"] == before
    assert db.get_review_note(path, "PAPER001") == winner["canonical"]["value"]
