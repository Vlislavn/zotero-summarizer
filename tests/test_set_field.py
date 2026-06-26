"""ZoteroWriter set_field — write/upsert/clear a single item field (used to stamp
the goal-blended rank into the sortable Call Number field)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError


@pytest.fixture
def writer(tmp_path: Path) -> ZoteroWriter:
    db_path = build_zotero_db(tmp_path / "zotero")
    add_library_item(db_path, item_key="PAPER1", title="A clinical agentic paper")
    return ZoteroWriter(db_path.parent)


def _call_number(writer: ZoteroWriter, item_key: str) -> str | None:
    conn = sqlite3.connect(str(writer.db_path)); conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT v.value FROM itemData d
            JOIN items i ON i.itemID = d.itemID
            JOIN fields f ON f.fieldID = d.fieldID
            JOIN itemDataValues v ON v.valueID = d.valueID
            WHERE i.key = ? AND f.fieldName = 'callNumber'
            """,
            (item_key,),
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def _change(value: str) -> dict:
    return {"id": 1, "change_type": "set_field", "item_key": "PAPER1",
            "payload_json": {"field": "callNumber", "value": value}}


def test_set_field_writes_call_number(writer: ZoteroWriter):
    res = writer.apply_changes([_change("zr0001")], create_backup=False)
    assert res["applied_ids"] == [1] and not res["failed"]
    assert _call_number(writer, "PAPER1") == "zr0001"


def test_set_field_upserts_replaces_prior_value(writer: ZoteroWriter):
    writer.apply_changes([_change("zr0007")], create_backup=False)
    writer.apply_changes([_change("zr0002")], create_backup=False)   # re-rank
    assert _call_number(writer, "PAPER1") == "zr0002"                 # replaced, not duplicated


def test_set_field_empty_value_clears(writer: ZoteroWriter):
    writer.apply_changes([_change("zr0003")], create_backup=False)
    writer.apply_changes([_change("")], create_backup=False)
    assert _call_number(writer, "PAPER1") is None


def test_set_field_unknown_field_fails(writer: ZoteroWriter):
    res = writer.apply_changes(
        [{"id": 1, "change_type": "set_field", "item_key": "PAPER1",
          "payload_json": {"field": "notARealField", "value": "x"}}],
        create_backup=False,
    )
    assert res["applied_ids"] == [] and len(res["failed"]) == 1
