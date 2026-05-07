from __future__ import annotations

import asyncio
import html
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.contracts import PendingChange
from zotero_summarizer.domain import ChangeStatus, ReadingPriority
from zotero_summarizer.models import (
    PendingChangeMutationRequest,
    PendingChangeUpdateRequest,
    PendingChangesResponse,
    PendingPriorityOverrideRequest,
    SummarizeResponse,
)
from zotero_summarizer.services._common import LOGGER, unique_non_empty_strings
from zotero_summarizer.storage import repositories as triage_db


PRIORITY_TAG_CASEFOLDED = {
    f"zs:{ReadingPriority.MUST_READ.value}".casefold(),
    f"zs:{ReadingPriority.SHOULD_READ.value}".casefold(),
    f"zs:{ReadingPriority.COULD_READ.value}".casefold(),
    f"zs:{ReadingPriority.DONT_READ.value}".casefold(),
}
PRIORITY_TAGS = {
    ReadingPriority.MUST_READ.value,
    ReadingPriority.SHOULD_READ.value,
    ReadingPriority.COULD_READ.value,
    ReadingPriority.DONT_READ.value,
}


class PendingChangePlanner:
    """Build reviewed Zotero changes from triage outcomes."""

    @staticmethod
    def normalize_tags(values: list[str] | None) -> list[str]:
        return unique_non_empty_strings(values or [])

    def priority_tag_payload(self, current_tags: list[str], new_priority: str) -> dict[str, list[str]]:
        priority = new_priority if new_priority in PRIORITY_TAGS else ReadingPriority.COULD_READ.value
        return build_priority_tag_change(current_tags, priority)

    def triage_changes(
        self,
        *,
        item_key: str,
        item_title: str,
        reading_priority: str,
        tags: list[str],
        note_html: str,
        suggested_collections: list[str] | None = None,
    ) -> list[PendingChange]:
        normalized_tags = normalize_change_tags(tags, reading_priority)
        changes: list[PendingChange] = [
            PendingChange(
                item_key=item_key,
                item_title=item_title,
                change_type="tag_changes",
                payload={"add_tags": normalized_tags, "remove_tags": []},
            )
        ]

        safe_note = str(note_html or "").strip()
        if safe_note:
            changes.append(
                PendingChange(
                    item_key=item_key,
                    item_title=item_title,
                    change_type="add_note",
                    payload={
                        "note_title": f"Triage: {item_title[:80]}",
                        "note_html": safe_note,
                    },
                )
            )

        for collection in normalize_collection_suggestions(suggested_collections or []):
            changes.append(
                PendingChange(
                    item_key=item_key,
                    item_title=item_title,
                    change_type="add_to_collection",
                    payload={"collection_path": collection},
                )
            )

        return changes

    @staticmethod
    def to_repository_rows(changes: list[PendingChange]) -> list[dict[str, Any]]:
        return [
            {
                "change_type": change.change_type,
                "payload": dict(change.payload),
            }
            for change in changes
        ]


def normalize_change_tags(tags: list[str], reading_priority: str) -> list[str]:
    normalized = unique_non_empty_strings(tags)
    priority_tag = f"zs:{reading_priority}"
    if priority_tag.casefold() not in {tag.casefold() for tag in normalized}:
        normalized.insert(0, priority_tag)
    return normalized


def normalize_tag_values(value: Any) -> list[str]:
    if value is None:
        candidates: list[str] = []
    elif isinstance(value, list):
        candidates = [str(item or "") for item in value]
    elif isinstance(value, str):
        candidates = [part.strip() for part in value.split(",")]
    else:
        candidates = [str(value)]
    return unique_non_empty_strings(candidates)


def build_priority_tag_change(current_tags: list[str], new_priority: str) -> dict[str, list[str]]:
    target_tag = f"zs:{new_priority}"
    target_folded = target_tag.casefold()

    has_target = False
    remove_tags: list[str] = []
    seen_remove: set[str] = set()
    for tag in current_tags:
        folded = tag.casefold()
        if folded == target_folded:
            has_target = True
        if folded in PRIORITY_TAG_CASEFOLDED and folded != target_folded and folded not in seen_remove:
            seen_remove.add(folded)
            remove_tags.append(tag)

    add_tags = [] if has_target else [target_tag]
    return {"add_tags": add_tags, "remove_tags": remove_tags}


def normalize_pending_tag_payload(payload: dict[str, Any]) -> dict[str, list[str]]:
    add_tags = normalize_tag_values(payload.get("add_tags", []))
    remove_tags = normalize_tag_values(payload.get("remove_tags", []))
    add_folded = {tag.casefold() for tag in add_tags}
    filtered_remove = [tag for tag in remove_tags if tag.casefold() not in add_folded]
    return {"add_tags": add_tags, "remove_tags": filtered_remove}


def normalize_pending_collection_payload(payload: dict[str, Any]) -> dict[str, str]:
    collection_key = str(payload.get("collection_key") or "").strip()
    collection_path = str(
        payload.get("collection_path")
        or payload.get("collection_name")
        or ""
    ).strip()
    if not collection_key and not collection_path:
        raise ValueError("collection_key or collection_path must be provided")

    normalized: dict[str, str] = {}
    if collection_key:
        normalized["collection_key"] = collection_key
    if collection_path:
        normalized["collection_path"] = collection_path
    return normalized


def normalize_pending_note_payload(payload: dict[str, Any]) -> dict[str, str]:
    note_title = str(payload.get("note_title") or "").strip() or "Triage note"
    note_html = str(payload.get("note_html") or payload.get("note_text") or "").strip()
    if not note_html:
        raise ValueError("note_html must be a non-empty string")
    return {"note_title": note_title, "note_html": note_html}


def effective_tag_payload(current_tags: list[str], payload: dict[str, list[str]]) -> dict[str, list[str]]:
    current_folded = {tag.casefold() for tag in current_tags}
    add_tags = [tag for tag in payload.get("add_tags", []) if tag.casefold() not in current_folded]
    add_folded = {tag.casefold() for tag in add_tags}
    remove_tags = [
        tag
        for tag in payload.get("remove_tags", [])
        if tag.casefold() in current_folded and tag.casefold() not in add_folded
    ]
    return {"add_tags": add_tags, "remove_tags": remove_tags}


def normalize_collection_suggestions(collections: list[str]) -> list[str]:
    return unique_non_empty_strings(collections)


def build_triage_note_html(title: str, summary: SummarizeResponse) -> str:
    key_sections = "".join(
        f"<li>{html.escape(section)}</li>" for section in summary.key_sections_to_read if str(section).strip()
    )
    findings = "".join(f"<li>{html.escape(finding)}</li>" for finding in summary.key_findings if str(finding).strip())
    return "".join(
        [
            "<h1>Zotero Summarizer Triage Note</h1>",
            f"<p><strong>Title:</strong> {html.escape(title)}</p>",
            f"<p><strong>Priority:</strong> {html.escape(summary.reading_priority)}</p>",
            f"<p><strong>Composite score:</strong> {summary.composite_relevance_score:.2f}</p>",
            "<h2>Executive Summary</h2>",
            f"<p>{html.escape(summary.executive_summary)}</p>",
            "<h2>Should I Deep Read?</h2>",
            f"<p>{html.escape(summary.should_deep_read)}</p>",
            "<h2>Key Sections</h2>",
            f"<ul>{key_sections or '<li>None</li>'}</ul>",
            "<h2>Key Findings</h2>",
            f"<ul>{findings or '<li>None</li>'}</ul>",
            "<h2>Triage Rationale</h2>",
            f"<p>{html.escape(summary.triage_rationale)}</p>",
        ]
    )


def queue_changes_for_item(item_key: str, title: str, summary: SummarizeResponse) -> int:
    planner = PendingChangePlanner()
    changes = planner.triage_changes(
        item_key=item_key,
        item_title=title,
        reading_priority=summary.reading_priority,
        tags=summary.tags,
        note_html=build_triage_note_html(title, summary),
        suggested_collections=summary.suggested_collections,
    )
    return triage_db.insert_pending_changes(item_key=item_key, item_title=title, changes=planner.to_repository_rows(changes))


async def list_pending_changes(status: str = ChangeStatus.PENDING.value, limit: int = 500) -> PendingChangesResponse:
    safe_status = str(status or "").strip().lower()
    if not safe_status:
        safe_status = ChangeStatus.PENDING.value
    elif safe_status == "all":
        safe_status = None
    items = await asyncio.to_thread(triage_db.get_pending_changes, safe_status, limit)
    return PendingChangesResponse(items=items)


async def update_pending_change(change_id: int, req: PendingChangeUpdateRequest) -> dict[str, Any]:
    safe_change_id = int(change_id)
    if safe_change_id <= 0:
        raise APIError(error="validation_error", message="change_id must be a positive integer", status_code=422)

    rows = await asyncio.to_thread(triage_db.get_pending_changes_by_ids, [safe_change_id], ChangeStatus.PENDING.value)
    if not rows:
        raise APIError(error="not_found", message="Pending change not found", status_code=404)

    change_type = str(rows[0].get("change_type") or "").strip()
    try:
        if change_type == "tag_changes":
            payload: dict[str, Any] = normalize_pending_tag_payload(req.payload)
        elif change_type in {"add_to_collection", "remove_from_collection"}:
            payload = normalize_pending_collection_payload(req.payload)
        elif change_type == "add_note":
            payload = normalize_pending_note_payload(req.payload)
        else:
            raise APIError(
                error="validation_error",
                message=f"{change_type} cannot be edited from the UI",
                status_code=422,
            )
    except ValueError as exc:
        raise APIError(error="validation_error", message=str(exc), status_code=422) from exc

    updated = await asyncio.to_thread(triage_db.update_pending_change_payload, safe_change_id, payload)
    if not updated:
        raise APIError(error="conflict", message="Pending change is no longer editable", status_code=409)

    LOGGER.info("Pending change updated change_id=%s change_type=%s", safe_change_id, change_type)
    return {"updated": 1, "change_id": safe_change_id, "change_type": change_type, "payload": payload}


async def pending_change_count(status: str = ChangeStatus.PENDING.value) -> dict[str, int]:
    count = await asyncio.to_thread(triage_db.get_pending_change_count, status)
    return {"count": count}


async def queue_priority_override(req: PendingPriorityOverrideRequest) -> dict[str, Any]:
    from zotero_summarizer.services.zotero import get_zotero_reader_or_raise

    reader = get_zotero_reader_or_raise()
    detail = await asyncio.to_thread(reader.get_item_detail, req.item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {req.item_key} not found", status_code=404)

    current_tags = [str(tag or "").strip() for tag in (detail.get("tags") or []) if str(tag or "").strip()]
    payload = build_priority_tag_change(current_tags, req.new_priority)
    if not payload["add_tags"] and not payload["remove_tags"]:
        return {
            "queued": 0,
            "item_key": req.item_key,
            "new_priority": req.new_priority,
            "message": "Priority tag already set",
        }

    item_title = req.item_title or str(detail.get("title") or req.item_key)
    queued = await asyncio.to_thread(
        triage_db.insert_pending_changes,
        req.item_key,
        item_title,
        [{"change_type": "tag_changes", "payload": payload}],
    )
    return {
        "queued": queued,
        "item_key": req.item_key,
        "new_priority": req.new_priority,
        "add_tags": payload["add_tags"],
        "remove_tags": payload["remove_tags"],
    }


async def reject_pending_changes(req: PendingChangeMutationRequest) -> dict[str, int]:
    updated = await asyncio.to_thread(triage_db.set_pending_changes_status, req.change_ids, ChangeStatus.REJECTED.value, "")
    return {"updated": updated}


async def apply_pending_changes(req: PendingChangeMutationRequest) -> dict[str, Any]:
    from zotero_summarizer.services.corpus import refresh_corpus_items_by_keys
    from zotero_summarizer.services.zotero import get_zotero_writer_or_raise

    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running() and not req.force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    changes = await asyncio.to_thread(triage_db.get_pending_changes_by_ids, req.change_ids, ChangeStatus.PENDING.value)
    if not changes:
        return {"applied": 0, "failed": 0, "backup_path": None, "failed_items": []}

    result = await asyncio.to_thread(writer.apply_changes, changes, True)
    applied_ids = [int(change_id) for change_id in result.get("applied_ids", []) if int(change_id) > 0]
    failed_items = list(result.get("failed", []))
    applied_id_set = set(applied_ids)

    if applied_ids:
        await asyncio.to_thread(triage_db.set_pending_changes_status, applied_ids, ChangeStatus.APPLIED.value, "")

    for failed in failed_items:
        failed_id = int(failed.get("id") or 0)
        if failed_id > 0:
            await asyncio.to_thread(
                triage_db.set_pending_changes_status,
                [failed_id],
                ChangeStatus.FAILED.value,
                str(failed.get("error") or ""),
            )

    refreshed_item_keys = [
        str(change.get("item_key") or "").strip()
        for change in changes
        if int(change.get("id") or 0) in applied_id_set and str(change.get("item_key") or "").strip()
    ]

    inbox_removed = 0
    if refreshed_item_keys:
        try:
            inbox_removed = await asyncio.to_thread(
                writer.remove_items_from_collection,
                refreshed_item_keys,
                "Inbox",
                True,
            )
        except Exception:
            LOGGER.warning("Failed removing applied items from Inbox collection", exc_info=True)
        await refresh_corpus_items_by_keys(refreshed_item_keys)

    LOGGER.info(
        "Pending apply completed requested=%s applied=%s failed=%s inbox_removed=%s",
        len(req.change_ids),
        len(applied_ids),
        len(failed_items),
        inbox_removed,
    )
    return {
        "applied": len(applied_ids),
        "failed": len(failed_items),
        "backup_path": result.get("backup_path"),
        "failed_items": failed_items,
        "inbox_removed": inbox_removed,
    }
