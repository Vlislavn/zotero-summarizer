from __future__ import annotations

from zotero_summarizer.services import pending as pending_service
from zotero_summarizer.models import SummarizeResponse


def _build_summary(**overrides) -> SummarizeResponse:
    payload = {
        "executive_summary": "Concise summary.",
        "relevance_score": 4,
        "composite_relevance_score": 4.1,
        "reading_priority": "should_read",
        "tags": ["topic:agents"],
        "triage_rationale": "Strong match to goals.",
        "suggested_collections": ["Research > Agents"],
    }
    payload.update(overrides)
    return SummarizeResponse(**payload)


def test_normalize_collection_suggestions_deduplicates_and_trims():
    normalized = pending_service.normalize_collection_suggestions(
        [
            "  Research > Agents  ",
            "research > agents",
            "Benchmarks",
            "",
            "  ",
            "BENCHMARKS",
        ]
    )

    assert normalized == ["Research > Agents", "Benchmarks"]


def test_pending_queue_includes_collection_changes(monkeypatch):
    captured: dict[str, object] = {}

    def fake_insert_pending_changes(item_key: str, item_title: str, changes: list[dict[str, object]]) -> int:
        captured["item_key"] = item_key
        captured["item_title"] = item_title
        captured["changes"] = changes
        return len(changes)

    monkeypatch.setattr(pending_service.triage_db, "insert_pending_changes", fake_insert_pending_changes)

    summary = _build_summary(
        reading_priority="must_read",
        suggested_collections=["Research > Agents", "Research > Agents", "Top Papers"],
    )

    queued = pending_service.queue_changes_for_item("ABCD1234", "Example Paper", summary)

    assert queued == 4
    assert captured["item_key"] == "ABCD1234"
    assert captured["item_title"] == "Example Paper"

    changes = captured["changes"]
    assert isinstance(changes, list)
    assert [change["change_type"] for change in changes] == [
        "tag_changes",
        "add_note",
        "add_to_collection",
        "add_to_collection",
    ]

    assert changes[2]["payload"] == {"collection_path": "Research > Agents"}
    assert changes[3]["payload"] == {"collection_path": "Top Papers"}
