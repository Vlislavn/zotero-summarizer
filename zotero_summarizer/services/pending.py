from __future__ import annotations

import asyncio
import html
from datetime import datetime, timezone
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


# Phase 1.5: provenance constants. The /zs/feeds-v3 system tag and the v3
# comment header are how the user (and future agents) distinguish notes the
# daemon wrote from notes the user wrote. The user already has 215 prior
# v2-format notes; HTML comments survive Zotero's TinyMCE round-trips
# (verified by precedent in their library).
NOTE_VERSION = 3
NOTE_PROVENANCE_NAMESPACE = "zs"  # Continues the existing zotero-summarizer prefix.
NOTE_PROVENANCE_SOURCE = "feed-batch"
SYSTEM_TAG_FEEDS_V3 = "/zs/feeds-v3"


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


# --- Reading priority emojis surfaced in the note's headline -----------------
_PRIORITY_GLYPH = {
    "must_read": "🔥",
    "should_read": "👀",
    "could_read": "📎",
    "dont_read": "—",
}

# Zotero's note editor (TinyMCE) silently strips most non-trivial HTML — no CSS,
# no <div>, no <h1> (which it renders as document title). The note template uses
# ONLY <h2>, <p>, <ul>/<li>, <strong>, <em>. Adding any other tag will get
# dropped on display, so the template builder below is the single source of truth
# for note HTML.


def build_provenance_comment(
    *,
    run_id: str | None = None,
    source: str = NOTE_PROVENANCE_SOURCE,
    version: int = NOTE_VERSION,
) -> str:
    """Build the HTML comment that marks a note as agent-generated.

    Format mirrors the user's 215 prior v2 notes (verified to survive Zotero's
    TinyMCE round-trips), bumped to version=3 with `source=feed-batch` to
    distinguish daemon-written notes from older library-triage notes.

    The comment is parseable as `key=value;key=value;...` for any future tool
    that wants to grep notes by run_id, model, or version.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_run = (run_id or "").replace("-->", "").replace("<!--", "")
    safe_source = source.replace("-->", "").replace("<!--", "")
    fields = [
        f"{NOTE_PROVENANCE_NAMESPACE}:note_type=triage",
        f"version={int(version)}",
        f"generated_at={ts}",
        f"source={safe_source}",
    ]
    if safe_run:
        fields.append(f"run_id={safe_run}")
    return f"<!-- {';'.join(fields)} -->"


def build_triage_note_html(
    title: str,
    summary: SummarizeResponse,
    *,
    is_black_swan: bool = False,
    surprise_score: float | None = None,
    run_id: str | None = None,
    include_provenance: bool = True,
) -> str:
    """Render a concise Zotero-renderable triage note.

    Phase 1.5 v3 format (verified against the user's 215 prior agent notes in
    /Users/vladnikulin/Zotero/zotero.sqlite — TinyMCE preserves comments):
      - Leading <!-- zs:note_type=triage;version=3;... --> provenance marker.
      - NO <div class="zotero-note znv1"> wrapper (the user said v2 "looks
        poorly"; Zotero TinyMCE often strips <div> anyway).
      - NO "[ZS TRIAGE v2] 🔴 Article Analysis" heading (was the noise).
      - 3 sections + 1 footer, target <150 words rendered.
      - Only Zotero-safe HTML (<h2>, <p>, <ul>/<li>, <strong>, <em>).
      - Concrete metrics in key findings (the LLM is prompted for numbers).
      - Optional black-swan badge in the footer.

    The other 8 LLM-produced fields (controversial_points, methods, limitations,
    industry_academy_impact, etc.) stay in triage_history.db for inspection via
    the web UI — they're deliberately omitted from the note to keep it scannable.
    """
    glyph = _PRIORITY_GLYPH.get(summary.reading_priority, "•")
    priority_label = summary.reading_priority.replace("_", " ").title()

    # Verdict: prefer the LLM's triage rationale, fall back to should_deep_read,
    # then executive_summary, then a bare reference to the paper title so the
    # note is never empty.
    verdict = (summary.triage_rationale or summary.should_deep_read or summary.executive_summary or "").strip()
    if not verdict:
        verdict = f"Triaged paper: {title or 'Untitled'}."

    # Pick at most 3 key findings; skip blanks.
    findings = [f for f in (summary.key_findings or []) if str(f).strip()][:3]
    findings_html = "".join(f"<li>{html.escape(str(f))}</li>" for f in findings) or "<li><em>No specific findings extracted.</em></li>"

    # Relevance — keep to 1-2 sentences max.
    relevance = (summary.relevance_to_research or "").strip()
    if not relevance:
        relevance = "(No specific connection to your goals extracted.)"

    tags_preview = ", ".join(html.escape(t) for t in (summary.tags or [])[:3]) or "—"
    matched_goal = html.escape(summary.matched_goal or "—")

    parts: list[str] = []
    if include_provenance:
        parts.append(build_provenance_comment(run_id=run_id))
    parts.extend(
        [
            f"<h2>{html.escape(glyph)} {html.escape(priority_label)}</h2>",
            f"<p>{html.escape(verdict)}</p>",
            "<h2>Key findings</h2>",
            f"<ul>{findings_html}</ul>",
            "<h2>Relevance to my work</h2>",
            f"<p>{html.escape(relevance)}</p>",
        ]
    )

    # Compact footer in <em> to visually separate metadata from content.
    footer_bits = [
        f"score {summary.composite_relevance_score:.1f}",
        f"goal: {matched_goal}",
        f"tags: {tags_preview}",
    ]
    if is_black_swan:
        if surprise_score is not None:
            footer_bits.append(f"🦢 surprise {surprise_score:.2f}")
        else:
            footer_bits.append("🦢 surprise pick")
    parts.append(f"<p><em>{' · '.join(footer_bits)}</em></p>")

    return "".join(parts)


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
