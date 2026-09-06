"""Verdict comment → Zotero note: builder, upsert (insert/update), submit_verdict
wiring, and the provenance-list collection/tag/search filter intersect."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import golden as golden_routes
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.golden import hybrid_gt
from zotero_summarizer.services.zotero.pending import (
    USER_NOTE_MARKER,
    VERDICT_NOTE_MARKER,
    build_user_note_html,
    build_verdict_note_html,
)
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


def _query(writer: ZoteroWriter, sql: str, params=()):
    conn = sqlite3.connect(str(writer.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
def test_build_verdict_note_html_marked_and_escaped():
    h = build_verdict_note_html("must_read", "<b>x</b> & y")
    assert VERDICT_NOTE_MARKER in h  # find+replace marker present
    assert "🔥" in h  # must_read glyph
    assert "Must Read" in h
    assert "&lt;b&gt;" in h and "&amp;" in h  # comment HTML-escaped


# --------------------------------------------------------------------------- #
# upsert_note writer op (real Zotero-shaped sqlite)
# --------------------------------------------------------------------------- #
def _upsert_change(item_key: str, note_html: str):
    return {
        "id": 0,
        "item_key": item_key,
        "change_type": "upsert_note",
        "payload_json": {"note_html": note_html, "marker": VERDICT_NOTE_MARKER},
    }


def test_upsert_note_inserts_then_updates_in_place(tmp_path: Path):
    db = build_zotero_db(tmp_path / "zotero")
    add_library_item(db, item_key="ITEM0001", title="T")
    writer = ZoteroWriter(db.parent)

    r1 = writer.apply_changes(
        [
            _upsert_change(
                "ITEM0001", build_verdict_note_html("must_read", "first comment")
            )
        ],
        create_backup=False,
    )
    assert r1["failed"] == [], r1["failed"]
    notes = _query(writer, "SELECT note FROM itemNotes")
    assert len(notes) == 1
    assert "first comment" in notes[0]["note"]
    assert VERDICT_NOTE_MARKER in notes[0]["note"]

    # Re-save → SAME note replaced, never duplicated.
    r2 = writer.apply_changes(
        [
            _upsert_change(
                "ITEM0001", build_verdict_note_html("should_read", "second comment")
            )
        ],
        create_backup=False,
    )
    assert r2["failed"] == [], r2["failed"]
    notes = _query(writer, "SELECT note FROM itemNotes")
    assert len(notes) == 1
    assert "second comment" in notes[0]["note"]
    assert "first comment" not in notes[0]["note"]


# --------------------------------------------------------------------------- #
# submit_verdict wiring
# --------------------------------------------------------------------------- #
def _patch_verdict_basics(monkeypatch, *, note_fn):
    monkeypatch.setattr(golden_routes, "_load_all", lambda: [])
    monkeypatch.setattr(
        golden_routes.verdict_effects, "append_training_row", lambda *a, **k: None
    )
    monkeypatch.setattr(
        golden_routes.verdict_effects,
        "add_feed_verdict_to_library",
        lambda *_a: {
            "added_to_library": False,
            "add_status": "not_applicable",
            "add_error": None,
        },
    )
    with repositories.with_db_path(golden_routes._db_path()):
        repositories.init_db()
    monkeypatch.setattr(
        golden_routes.verdict_effects, "zotero_set_label_tag", lambda *_a: None
    )
    monkeypatch.setattr(
        golden_routes.verdict_effects, "zotero_upsert_verdict_note", note_fn
    )


def test_submit_verdict_writes_note_when_comment(monkeypatch):
    calls = []
    _patch_verdict_basics(
        monkeypatch, note_fn=lambda ik, up, c: calls.append((ik, up, c))
    )
    out = asyncio.run(
        golden_routes.submit_verdict(
            golden_routes.VerdictRequest(
                item_key="K1", user_priority="must_read", comment="useful"
            )
        )
    )
    assert out["note_written"] is True and out["note_error"] is None
    assert calls == [("K1", "must_read", "useful")]


def test_submit_verdict_skips_note_when_empty_comment(monkeypatch):
    calls = []
    _patch_verdict_basics(monkeypatch, note_fn=lambda *a: calls.append(a))
    out = asyncio.run(
        golden_routes.submit_verdict(
            golden_routes.VerdictRequest(
                item_key="K1", user_priority="could_read", comment="   "
            )
        )
    )
    assert out["note_written"] is False and out["note_error"] is None
    assert calls == []  # whitespace-only comment writes no note


def test_submit_verdict_note_failure_does_not_block_verdict(monkeypatch):
    def boom(*_a):
        raise RuntimeError("Zotero is open")

    _patch_verdict_basics(monkeypatch, note_fn=boom)
    out = asyncio.run(
        golden_routes.submit_verdict(
            golden_routes.VerdictRequest(
                item_key="K1", user_priority="must_read", comment="x"
            )
        )
    )
    assert out["id"] == 1  # verdict still durably saved
    assert out["note_written"] is False
    assert "Zotero is open" in out["note_error"]


def test_submit_verdict_swallows_optional_zotero_unavailable(monkeypatch):
    def unavailable(*_a):
        raise APIError(
            error="zotero_unavailable",
            message="Zotero library is not configured",
            status_code=503,
        )

    _patch_verdict_basics(monkeypatch, note_fn=unavailable)
    monkeypatch.setattr(
        golden_routes.verdict_effects, "zotero_set_label_tag", unavailable
    )

    out = asyncio.run(
        golden_routes.submit_verdict(
            golden_routes.VerdictRequest(
                item_key="K1", user_priority="must_read", comment="x"
            )
        )
    )

    assert out["id"] == 1
    assert out["label_written"] is False
    assert out["label_error"] is None
    assert out["note_written"] is False
    assert out["note_error"] is None


def _submit_feed_verdict(monkeypatch, *, item_key, priority, add_result):
    notes, labels = [], []
    _patch_verdict_basics(
        monkeypatch,
        note_fn=lambda ik, up, comment: notes.append((ik, up, comment)),
    )
    with sqlite3.connect(golden_routes._db_path()) as conn:
        conn.execute(
            "INSERT INTO processed_feed_items(feed_library_id, feed_item_id, stable_feed_key, guid, title, decision, run_id, materialized_zotero_key) VALUES (2, 42, ?, 'g', 'Paper', 'auto_materialized', 'run', ?)",
            (item_key, add_result.get("_zotero_key")),
        )
    monkeypatch.setattr(
        golden_routes.verdict_effects,
        "zotero_set_label_tag",
        lambda ik, up: labels.append((ik, up)),
    )
    monkeypatch.setattr(
        golden_routes.verdict_effects,
        "add_feed_verdict_to_library",
        lambda *_a: dict(add_result),
    )
    out = asyncio.run(
        golden_routes.submit_verdict(
            golden_routes.VerdictRequest(
                item_key=item_key, user_priority=priority, comment="why"
            ),
        )
    )
    return out, notes, labels


def test_new_feed_item_comment_uses_materialized_zotero_key(monkeypatch):
    out, notes, labels = _submit_feed_verdict(
        monkeypatch,
        item_key="feed:g:different-story",
        priority="must_read",
        add_result={
            "added_to_library": True,
            "add_status": "added",
            "add_error": None,
            "_zotero_key": "REAL0001",
        },
    )
    assert notes == [("REAL0001", "must_read", "why")]
    # Revalidate current state after creation; the real setter skips an already-set tag.
    assert labels == [("REAL0001", "must_read")]
    assert out["label_written"] is out["note_written"] is True
    assert "_zotero_key" not in out


def test_existing_materialized_feed_mirrors_negative_to_real_key(monkeypatch):
    out, notes, labels = _submit_feed_verdict(
        monkeypatch,
        item_key="feed:42",
        priority="dont_read",
        add_result={
            "added_to_library": False,
            "add_status": "already_in_library",
            "add_error": None,
            "_zotero_key": "REAL0002",
        },
    )
    assert notes == [("REAL0002", "dont_read", "why")]
    assert labels == [("REAL0002", "dont_read")]
    assert out["label_written"] is out["note_written"] is True


def test_unmaterialized_negative_feed_comment_stays_local_without_warning(monkeypatch):
    out, notes, labels = _submit_feed_verdict(
        monkeypatch,
        item_key="feed:g:unmaterialized",
        priority="dont_read",
        add_result={
            "added_to_library": False,
            "add_status": "not_applicable",
            "add_error": None,
            "_zotero_key": None,
        },
    )
    assert notes == labels == []
    assert out["label_written"] is out["note_written"] is False
    assert out["label_error"] is out["note_error"] is None


# --------------------------------------------------------------------------- #
# Review notes: builder, storage round-trip, save_review_note wiring
# --------------------------------------------------------------------------- #
def test_build_user_note_html_marked_and_escaped():
    h = build_user_note_html("para one\n\n<b>x</b> & y")
    assert USER_NOTE_MARKER in h
    assert "My notes" in h
    assert "<p>para one</p>" in h  # blank line → separate paragraph
    assert (
        "<p>&lt;b&gt;x&lt;/b&gt; &amp; y</p>" in h
    )  # 2nd para its OWN wrapped, escaped <p>
    assert "<script>" not in h and "<b>" not in h  # no raw markup leaks into the note


def _init_triage_db(path: Path) -> Path:
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)
    return path


def test_review_note_upsert_get_and_replace_in_place(tmp_path: Path):
    db = _init_triage_db(tmp_path / "triage.db")
    assert repositories.get_review_note(db, "K1") is None  # absent → None
    repositories.upsert_review_note(db, "K1", "first thoughts")
    assert repositories.get_review_note(db, "K1") == "first thoughts"
    repositories.upsert_review_note(db, "K1", "revised")  # UPSERT, one row
    assert repositories.get_review_note(db, "K1") == "revised"
    rows = (
        sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM review_notes").fetchone()
    )
    assert rows[0] == 1


def _patch_note_basics(monkeypatch, *, mirror_fn):
    """Returns the list that records every local upsert_review_note call, so a test
    proves the durable save actually happened with the right (item_key, note) —
    not just that the Zotero mirror spy fired."""
    saves: list[tuple] = []
    monkeypatch.setattr(golden_routes, "_db_path", lambda: Path("/unused"))
    monkeypatch.setattr(
        repositories,
        "upsert_review_note",
        lambda _db, ik, note: saves.append((ik, note)),
    )
    monkeypatch.setattr(
        golden_routes.verdict_effects, "zotero_upsert_user_note", mirror_fn
    )
    return saves


def test_save_review_note_mirrors_to_zotero(monkeypatch):
    calls = []
    saves = _patch_note_basics(
        monkeypatch, mirror_fn=lambda ik, note: calls.append((ik, note))
    )
    out = asyncio.run(
        golden_routes.save_review_note(
            golden_routes.ReviewNoteRequest(item_key="K1", note="jot")
        )
    )
    assert out == {"saved": True, "note_written": True, "note_error": None}
    assert saves == [("K1", "jot")]  # the DURABLE local save ran with the right args
    assert calls == [("K1", "jot")]  # and the Zotero mirror got the same


def test_save_review_note_mirror_failure_still_saves(monkeypatch):
    def boom(*_a):
        raise RuntimeError("Zotero is open")

    saves = _patch_note_basics(monkeypatch, mirror_fn=boom)
    out = asyncio.run(
        golden_routes.save_review_note(
            golden_routes.ReviewNoteRequest(item_key="K1", note="jot")
        )
    )
    assert out["saved"] is True and out["note_written"] is False
    assert "Zotero is open" in out["note_error"]
    assert saves == [("K1", "jot")]  # local save happened DESPITE the mirror failing


def test_save_review_note_swallows_optional_zotero_unavailable(monkeypatch):
    def unavailable(*_a):
        raise APIError(
            error="zotero_unavailable",
            message="Zotero library is not configured",
            status_code=503,
        )

    saves = _patch_note_basics(monkeypatch, mirror_fn=unavailable)
    out = asyncio.run(
        golden_routes.save_review_note(
            golden_routes.ReviewNoteRequest(item_key="K1", note="jot")
        )
    )
    # A missing Zotero DB is an optional-mirror miss, not an error surfaced to the user.
    assert out == {"saved": True, "note_written": False, "note_error": None}
    assert saves == [("K1", "jot")]


def test_save_review_note_feed_key_skips_zotero_mirror(monkeypatch):
    def _boom(*_a):
        raise AssertionError("feed key has no Zotero item — must not mirror")

    saves = _patch_note_basics(monkeypatch, mirror_fn=_boom)
    out = asyncio.run(
        golden_routes.save_review_note(
            golden_routes.ReviewNoteRequest(item_key="feed:123", note="jot")
        )
    )
    assert out == {"saved": True, "note_written": False, "note_error": None}
    assert saves == [
        ("feed:123", "jot")
    ]  # still saved locally, mirror correctly skipped


def test_save_review_note_blank_item_key_is_422(monkeypatch):
    saves = _patch_note_basics(monkeypatch, mirror_fn=lambda *_a: None)
    try:
        asyncio.run(
            golden_routes.save_review_note(
                golden_routes.ReviewNoteRequest(item_key="   ", note="jot")
            )
        )
        raise AssertionError("expected APIError for whitespace item_key")
    except APIError as exc:
        assert exc.status_code == 422
    assert saves == []  # never reached the storage layer


# --------------------------------------------------------------------------- #
# provenance list filtering (collection/tag/search → reader candidate intersect)
# --------------------------------------------------------------------------- #
def _prov(key, pri="must_read"):
    return SimpleNamespace(
        item_key=key,
        title=f"T{key}",
        persisted_priority=pri,
        derived_priority=pri,
        derived_score=3.0,
        is_direct_user_verdict=False,
        is_manual_override=False,
        flags=[],
    )


def test_list_all_intersects_zotero_candidate_keys(monkeypatch):
    monkeypatch.setattr(
        golden_routes, "_load_all", lambda: [_prov("A"), _prov("B"), _prov("C")]
    )
    monkeypatch.setattr(hybrid_gt, "load_user_verdicts", lambda _db: {})
    monkeypatch.setattr(
        golden_routes.label_provenance, "flag_summary", lambda _provs: {}
    )
    monkeypatch.setattr(
        golden_routes, "_zotero_candidate_keys", lambda **_k: {"A", "C"}
    )

    out = asyncio.run(golden_routes.list_all(collection="COLL"))
    assert {it["item_key"] for it in out["items"]} == {"A", "C"}
    assert out["total_matched"] == 2


def test_list_all_no_filter_skips_reader(monkeypatch):
    monkeypatch.setattr(golden_routes, "_load_all", lambda: [_prov("A"), _prov("B")])
    monkeypatch.setattr(hybrid_gt, "load_user_verdicts", lambda _db: {})
    monkeypatch.setattr(
        golden_routes.label_provenance, "flag_summary", lambda _provs: {}
    )

    def _boom(**_k):
        raise AssertionError(
            "reader must not be queried without a collection/tag/search filter"
        )

    monkeypatch.setattr(golden_routes, "_zotero_candidate_keys", _boom)
    out = asyncio.run(golden_routes.list_all())
    assert out["total_matched"] == 2
