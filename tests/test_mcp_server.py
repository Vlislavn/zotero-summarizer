from __future__ import annotations

from zotero_summarizer.mcp import (
    _base_item_update_payload,
    _decode_cursor,
    _decode_search_cursor,
    _encode_search_cursor,
    _extract_data_or_error,
    _parse_pending_change,
    _parse_response_json,
    _require_non_empty_text,
    _triage_from_result_row,
)


def test_decode_cursor_defaults_to_zero_for_invalid_values():
    assert _decode_cursor(None) == 0
    assert _decode_cursor("") == 0
    assert _decode_cursor("abc") == 0
    assert _decode_cursor("-3") == 0
    assert _decode_cursor("12") == 12


def test_search_cursor_roundtrip_supports_filtered_offset():
    assert _decode_search_cursor(None) == (0, 0)
    assert _decode_search_cursor("15") == (15, 0)
    assert _decode_search_cursor("15:20") == (15, 20)
    assert _encode_search_cursor(7, 0) == "7"
    assert _encode_search_cursor(7, 3) == "7:3"


def test_parse_response_json_accepts_dict_and_json_string():
    assert _parse_response_json({"a": 1}) == {"a": 1}
    assert _parse_response_json('{"a": 1}') == {"a": 1}
    assert _parse_response_json("not-json") == {}


def test_extract_data_or_error_returns_data_or_passthrough_error():
    data, error = _extract_data_or_error({"ok": True, "data": {"k": "v"}})
    assert data == {"k": "v"}
    assert error is None

    data, error = _extract_data_or_error({"ok": False, "error": {"code": "x"}})
    assert data is None
    assert error == {"ok": False, "error": {"code": "x"}}


def test_parse_pending_change_parses_payload_json():
    parsed = _parse_pending_change(
        {
            "id": 42,
            "item_key": "ABC123",
            "item_title": "Paper",
            "change_type": "tag_changes",
            "payload_json": '{"add_tags": ["must_read"]}',
            "status": "pending",
        }
    )

    assert parsed["change_id"] == 42
    assert parsed["item_key"] == "ABC123"
    assert parsed["change_type"] == "tag_changes"
    assert parsed["payload"] == {"add_tags": ["must_read"]}


def test_require_non_empty_text_returns_validation_error_for_blank_values():
    value, error = _require_non_empty_text("  ", "item_key")
    assert value is None
    assert error is not None
    assert error["ok"] is False
    assert error["error"]["code"] == "validation_error"

    value, error = _require_non_empty_text("ABC123", "item_key")
    assert value == "ABC123"
    assert error is None


def test_base_item_update_payload_uses_fallback_item_key():
    payload = _base_item_update_payload({"updated": 2, "message": "ok"}, "ITEM-1")
    assert payload["item_key"] == "ITEM-1"
    assert payload["updated"] == 2
    assert payload["message"] == "ok"


def test_triage_from_result_row_uses_response_json_fallback():
    row = {
        "response_json": '{"relevance_score": 4, "composite_relevance_score": 4.3, "reading_priority": "should_read", "triage_confidence": 0.8, "matched_goal": "agentic systems"}',
        "created_at": "2026-04-05T00:00:00Z",
    }

    triage = _triage_from_result_row(row)

    assert triage is not None
    assert triage["relevance_score"] == 4
    assert triage["composite_score"] == 4.3
    assert triage["reading_priority"] == "should_read"
    assert triage["confidence"] == 0.8
    assert triage["matched_goal"] == "agentic systems"
