"""ZoteroWriter add_attachment — create a native imported_url PDF attachment
(file copied into storage/<key>/ + sync-correct itemAttachments row)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter


@pytest.fixture
def writer_and_pdf(tmp_path: Path):
    db_path = build_zotero_db(tmp_path / "zotero")
    add_library_item(db_path, item_key="PAPER1", title="An arXiv paper",
                     url="http://arxiv.org/abs/1706.03762")
    pdf = tmp_path / "cache" / "x.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\n" + b"z" * 4096)
    return ZoteroWriter(db_path.parent), pdf, db_path.parent


def _change(pdf: Path) -> dict:
    return {"id": 1, "change_type": "add_attachment", "item_key": "PAPER1",
            "payload_json": {"source_path": str(pdf), "filename": "1706.03762.pdf",
                             "source_url": "https://arxiv.org/pdf/1706.03762.pdf",
                             "title": "arXiv Full Text PDF"}}


def _attachment_row(db_path: Path) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT i.key, i.synced, ia.parentItemID, ia.linkMode, ia.contentType, ia.path, ia.syncState
               FROM items i JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
               JOIN itemAttachments ia ON ia.itemID = i.itemID
               WHERE it.typeName = 'attachment' LIMIT 1""",
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_add_attachment_writes_sync_correct_row_and_file(writer_and_pdf):
    writer, pdf, data_dir = writer_and_pdf
    res = writer.apply_changes([_change(pdf)], create_backup=False)
    assert res["applied_ids"] == [1] and not res["failed"]

    row = _attachment_row(writer.db_path)
    assert row is not None
    assert row["linkMode"] == 1               # imported_url
    assert row["contentType"] == "application/pdf"
    assert row["path"] == "storage:1706.03762.pdf"
    assert row["syncState"] == 0              # TO_UPLOAD — Zotero hashes + uploads
    assert row["synced"] == 0                 # new item uploads on next sync

    # File copied into storage/<KEY>/<filename> (Zotero's stored-attachment layout).
    stored = data_dir / "storage" / row["key"] / "1706.03762.pdf"
    assert stored.is_file() and stored.stat().st_size > 0


def test_add_attachment_missing_source_fails_cleanly(writer_and_pdf):
    writer, _pdf, _data_dir = writer_and_pdf
    res = writer.apply_changes(
        [{"id": 1, "change_type": "add_attachment", "item_key": "PAPER1",
          "payload_json": {"source_path": "/no/such/file.pdf", "filename": "x.pdf",
                           "source_url": "u", "title": "t"}}],
        create_backup=False,
    )
    assert res["applied_ids"] == [] and len(res["failed"]) == 1
    assert _attachment_row(writer.db_path) is None  # nothing written on failure


def test_add_attachment_unknown_parent_fails(writer_and_pdf):
    writer, pdf, _data_dir = writer_and_pdf
    res = writer.apply_changes(
        [{"id": 1, "change_type": "add_attachment", "item_key": "NOPE",
          "payload_json": {"source_path": str(pdf), "filename": "x.pdf", "source_url": "u", "title": "t"}}],
        create_backup=False,
    )
    assert res["applied_ids"] == [] and len(res["failed"]) == 1


@pytest.mark.parametrize(("name", "content_type", "expected"), [
    ("bad\n\t\r\x00\x7f\x85\u202ename/\\.pdf", "application/pdf", "badname.pdf"),
    ("a" * 125, "application/pdf", "a" * 116 + ".pdf"),
    ("b" * 125 + ".PDF", "application/pdf", "b" * 116 + ".pdf"),
    ("c" * 125 + ".png", "image/png", "c" * 116 + ".png"),
    (" étude.pdf ", "application/pdf", "étude.pdf"),
    ("", "application/pdf", "fulltext.pdf"),
    ("\n/\\", "image/png", "fulltext.png"),
    ("notes\n.txt", "text/plain", "notes.txt"),
    ("..", "text/plain", "fulltext"),
])
def test_attachment_filename_matches_stored_file(writer_and_pdf, name, content_type, expected):
    writer, pdf, data_dir = writer_and_pdf
    change = _change(pdf)
    change["payload_json"].update(filename=name, content_type=content_type)

    result = writer.apply_changes([change], create_backup=False)

    assert result["applied_ids"] == [1] and result["failed"] == []
    row = _attachment_row(writer.db_path)
    assert row["path"] == f"storage:{expected}"
    assert row["contentType"] == content_type
    stored = data_dir / "storage" / row["key"] / expected
    assert stored.read_bytes() == pdf.read_bytes()
