from __future__ import annotations

import pytest

from zotero_summarizer.storage.rows import PendingChangeRow


def _full_row() -> dict:
    return {
        "id": 7,
        "item_key": "ABC",
        "item_title": "Paper",
        "change_type": "tag_changes",
        "payload_json": '{"add_tags": ["x"]}',
        "status": "pending",
        "error_message": None,
        "created_at": "2026-05-24T00:00:00Z",
        "applied_at": None,
    }


def test_from_row_round_trips_to_legacy_dict_shape():
    raw = _full_row()
    out = PendingChangeRow.from_row(raw).to_dict()
    assert out == raw  # byte-for-byte legacy contract preserved


def test_payload_property_parses_json_string():
    row = PendingChangeRow.from_row(_full_row())
    assert row.payload == {"add_tags": ["x"]}


def test_payload_property_tolerates_dict_payload():
    raw = _full_row()
    raw["payload_json"] = {"add_tags": ["y"]}
    assert PendingChangeRow.from_row(raw).payload == {"add_tags": ["y"]}


def test_from_row_fails_loud_on_missing_column():
    raw = _full_row()
    del raw["status"]
    with pytest.raises(KeyError, match="status"):
        PendingChangeRow.from_row(raw)


def test_id_coerced_to_int():
    raw = _full_row()
    raw["id"] = "9"
    assert PendingChangeRow.from_row(raw).id == 9
