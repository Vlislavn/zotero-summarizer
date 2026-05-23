"""Verdict comment → Zotero note: builder, upsert (insert/update), submit_verdict
wiring, and the provenance-list collection/tag/search filter intersect."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.api.routes import golden as golden_routes
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.golden import hybrid_gt
from zotero_summarizer.services.zotero.pending import VERDICT_NOTE_MARKER, build_verdict_note_html
from zotero_summarizer.storage import repositories


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
    assert VERDICT_NOTE_MARKER in h          # find+replace marker present
    assert "🔥" in h                          # must_read glyph
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
        [_upsert_change("ITEM0001", build_verdict_note_html("must_read", "first comment"))],
        create_backup=False,
    )
    assert r1["failed"] == [], r1["failed"]
    notes = _query(writer, "SELECT note FROM itemNotes")
    assert len(notes) == 1
    assert "first comment" in notes[0]["note"]
    assert VERDICT_NOTE_MARKER in notes[0]["note"]

    # Re-save → SAME note replaced, never duplicated.
    r2 = writer.apply_changes(
        [_upsert_change("ITEM0001", build_verdict_note_html("should_read", "second comment"))],
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
    monkeypatch.setattr(golden_routes, "_append_verdict_golden", lambda *a, **k: None)
    monkeypatch.setattr(repositories, "insert_or_update_label_verdict", lambda *a, **k: 1)
    monkeypatch.setattr(
        repositories, "get_label_verdict",
        lambda *a, **k: {"original_derived_priority": "unknown", "created_at": "2026-05-23T00:00:00Z"},
    )
    monkeypatch.setattr(golden_routes, "zotero_upsert_verdict_note", note_fn)


def test_submit_verdict_writes_note_when_comment(monkeypatch):
    calls = []
    _patch_verdict_basics(monkeypatch, note_fn=lambda ik, up, c: calls.append((ik, up, c)))
    out = asyncio.run(golden_routes.submit_verdict(
        golden_routes.VerdictRequest(item_key="K1", user_priority="must_read", comment="useful")))
    assert out["note_written"] is True and out["note_error"] is None
    assert calls == [("K1", "must_read", "useful")]


def test_submit_verdict_skips_note_when_empty_comment(monkeypatch):
    calls = []
    _patch_verdict_basics(monkeypatch, note_fn=lambda *a: calls.append(a))
    out = asyncio.run(golden_routes.submit_verdict(
        golden_routes.VerdictRequest(item_key="K1", user_priority="could_read", comment="   ")))
    assert out["note_written"] is False and out["note_error"] is None
    assert calls == []  # whitespace-only comment writes no note


def test_submit_verdict_note_failure_does_not_block_verdict(monkeypatch):
    def boom(*_a):
        raise RuntimeError("Zotero is open")

    _patch_verdict_basics(monkeypatch, note_fn=boom)
    out = asyncio.run(golden_routes.submit_verdict(
        golden_routes.VerdictRequest(item_key="K1", user_priority="must_read", comment="x")))
    assert out["id"] == 1  # verdict still durably saved
    assert out["note_written"] is False
    assert "Zotero is open" in out["note_error"]


# --------------------------------------------------------------------------- #
# provenance list filtering (collection/tag/search → reader candidate intersect)
# --------------------------------------------------------------------------- #
def _prov(key, pri="must_read"):
    return SimpleNamespace(
        item_key=key, title=f"T{key}", persisted_priority=pri, derived_priority=pri,
        derived_score=3.0, is_direct_user_verdict=False, is_manual_override=False, flags=[],
    )


def test_list_all_intersects_zotero_candidate_keys(monkeypatch):
    monkeypatch.setattr(golden_routes, "_load_all", lambda: [_prov("A"), _prov("B"), _prov("C")])
    monkeypatch.setattr(hybrid_gt, "load_user_verdicts", lambda _db: {})
    monkeypatch.setattr(golden_routes.label_provenance, "flag_summary", lambda _provs: {})
    monkeypatch.setattr(golden_routes, "_zotero_candidate_keys", lambda **_k: {"A", "C"})

    out = asyncio.run(golden_routes.list_all(collection="COLL"))
    assert {it["item_key"] for it in out["items"]} == {"A", "C"}
    assert out["total_matched"] == 2


def test_list_all_no_filter_skips_reader(monkeypatch):
    monkeypatch.setattr(golden_routes, "_load_all", lambda: [_prov("A"), _prov("B")])
    monkeypatch.setattr(hybrid_gt, "load_user_verdicts", lambda _db: {})
    monkeypatch.setattr(golden_routes.label_provenance, "flag_summary", lambda _provs: {})

    def _boom(**_k):
        raise AssertionError("reader must not be queried without a collection/tag/search filter")

    monkeypatch.setattr(golden_routes, "_zotero_candidate_keys", _boom)
    out = asyncio.run(golden_routes.list_all())
    assert out["total_matched"] == 2
