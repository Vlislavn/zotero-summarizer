"""Tests for the MCP isolation against indirect prompt-injection-driven writes.

The defense lives in `zotero_summarizer.mcp.tools.pending`:
- `_is_restricted_change_type` flags any change_type starting with create_/inbox_/promote_
- `apply_pending_changes` filters out flagged IDs before forwarding to the apply endpoint
"""
from __future__ import annotations

from zotero_summarizer.mcp.tools.pending import (
    MCP_RESTRICTED_CHANGE_TYPE_PREFIXES,
    _is_restricted_change_type,
)


def test_restricted_prefixes_cover_phase_1_change_types():
    assert "create_" in MCP_RESTRICTED_CHANGE_TYPE_PREFIXES
    assert "inbox_" in MCP_RESTRICTED_CHANGE_TYPE_PREFIXES
    assert "promote_" in MCP_RESTRICTED_CHANGE_TYPE_PREFIXES
    # Phase 1.5: daemon-only operations must NEVER be applied via MCP.
    assert "mark_feed_" in MCP_RESTRICTED_CHANGE_TYPE_PREFIXES


def test_mark_feed_item_read_is_restricted():
    """Indirect prompt injection in an abstract must not be able to silence
    Zotero's unread badge by smuggling a `mark_feed_item_read` change."""
    assert _is_restricted_change_type("mark_feed_item_read") is True
    assert _is_restricted_change_type("MARK_FEED_ITEM_READ") is True


def test_create_item_from_feed_is_restricted():
    assert _is_restricted_change_type("create_item_from_feed") is True
    assert _is_restricted_change_type("CREATE_ITEM_FROM_FEED") is True
    assert _is_restricted_change_type("  create_anything  ") is True


def test_promote_from_inbox_is_restricted():
    assert _is_restricted_change_type("promote_from_inbox") is True


def test_existing_phase_0_change_types_are_NOT_restricted():
    """The four pre-existing change types must remain MCP-applyable."""
    for safe in ("tag_changes", "add_note", "add_to_collection", "remove_from_collection"):
        assert _is_restricted_change_type(safe) is False


def test_empty_or_unknown_change_type_not_restricted():
    assert _is_restricted_change_type("") is False
    assert _is_restricted_change_type("something_random") is False
    assert _is_restricted_change_type(None) is False  # type: ignore[arg-type]


def test_inbox_anything_blocked():
    """Even hypothetical future inbox_* types must be blocked at the MCP layer."""
    assert _is_restricted_change_type("inbox_dismiss") is True
    assert _is_restricted_change_type("inbox_") is True
