"""Three explicit human editors: durable SQL history, not Pending approval authority."""
from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models.api import (
    ZoteroItemCollectionUpdateRequest, ZoteroItemPriorityUpdateRequest, ZoteroItemTagUpdateRequest,
)
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.zotero.pending import (
    build_label_tag_change, effective_tag_payload, normalize_pending_tag_payload,
)
from zotero_summarizer.services.zotero.pending_confirmation import check_uncertainty
from zotero_summarizer.storage import pending_actions


def _current_tags(detail: dict) -> list[str]:
    return [str(tag or "").strip() for tag in (detail.get("tags") or []) if str(tag or "").strip()]


def _tag_plan(req: ZoteroItemTagUpdateRequest, detail: dict) -> tuple[dict, list[dict], dict]:
    requested = req.model_dump(exclude={"force"})
    payload = effective_tag_payload(_current_tags(detail), normalize_pending_tag_payload(requested))
    changed = bool(payload["add_tags"] or payload["remove_tags"])
    fields = {"updated": int(changed), **payload}
    if not changed:
        fields["message"] = "Tag update has no net changes"
    actions = [{"change_type": "tag_changes", "payload_json": payload}] if changed else []
    return fields, actions, requested


def _priority_plan(req: ZoteroItemPriorityUpdateRequest, detail: dict) -> tuple[dict, list[dict], dict]:
    requested = req.model_dump(exclude={"force"})
    payload = build_label_tag_change(_current_tags(detail), req.priority)
    if not payload["add_tags"] and not payload["remove_tags"]:
        return {"updated": 0, "priority": req.priority, "message": "Label tag already set"}, [], requested
    return ({"updated": 1, "priority": req.priority, **payload},
            [{"change_type": "tag_changes", "payload_json": payload}], requested)


def _collection_plan(req: ZoteroItemCollectionUpdateRequest) -> tuple[dict, list[dict], dict]:
    added = [ref.to_writer_payload() for ref in req.add]
    removed = [ref.to_writer_payload() for ref in req.remove]
    requested = {"add": added, "remove": removed}
    actions = [{"change_type": kind, "payload_json": payload}
               for kind, payloads in (("add_to_collection", added), ("remove_from_collection", removed))
               for payload in payloads]
    fields = {"updated": len(actions), "added": added, "removed": removed}
    if not actions:
        fields = {"updated": 0, "message": "Collection update has no changes"}
    return fields, actions, requested


def _plan(source: str, req, detail: dict) -> tuple[dict, list[dict], dict]:
    if source == "direct_tags":
        return _tag_plan(req, detail)
    if source == "direct_priority":
        return _priority_plan(req, detail)
    if source == "direct_collections":
        return _collection_plan(req)
    raise ValueError("Unknown direct editor")


def _apply_edit(source: str, item_key: str, req, reader: ZoteroReader, writer: ZoteroWriter) -> dict:
    safe_key = str(item_key or "").strip()
    if not safe_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)
    uncertain = None
    with _review_cache.review_publication_lock(require_publication=False):
        try:
            with pending_actions.locked_queue() as conn:
                target = writer.target_identity()
                check_uncertainty(conn, [safe_key], target)
                detail = reader.get_item_detail(safe_key)
                if detail is None:
                    raise APIError(error="not_found", message="Item not found", status_code=404)
                if writer.is_connector_running() and not req.force:
                    return {"error": "zotero_running", "requires_force": True,
                            "message": "Zotero appears to be running; close Zotero or confirm force apply."}
                fields, actions, requested = _plan(source, req, detail)
                fields = {**fields, "item_key": safe_key}
                if not actions:
                    return {**fields, "item": reader.get_item_detail(safe_key)}
                backup = writer.backup_database(expected_target=target)
                if not isinstance(backup, str) or not backup:
                    raise ValueError("Direct writes require a retained backup reference")
                claimed = pending_actions.claim_direct(conn, safe_key, str(detail.get("title") or ""), actions, {
                    "event": "direct_requested", "actor": "current_user", "source": source,
                    "requested": requested, "target": target.model_dump(),
                    "recovery": "backup_only", "backup_path": backup,
                })
                conn.commit()  # durable applying intent before the separate native transaction
                uncertain = {"action_ids": [row["id"] for row in claimed], "backup_path": backup,
                             "delivery_status": "uncertain"}
                result = writer.apply_changes([dict(row) for row in claimed], False,
                                              precreated_backup=backup, expected_target=target)
                pending_actions.finalize_direct(conn, claimed, result)
        except Exception as exc:
            if uncertain is None:
                raise
            raise APIError(error="zotero_write_uncertain", status_code=409,
                           message="Native delivery or receipt finalization could not be confirmed",
                           details=uncertain) from exc
        # The queue context has committed every terminal receipt and closed before this response.
        failures = result["failed"]
        if failures:
            raise APIError(error="zotero_write_failed", message=failures[0]["error"], status_code=500, details={
                **uncertain, "applied_ids": result["applied_ids"],
                "failed_ids": [failure["id"] for failure in failures],
                "delivery_status": "partially_applied" if result["applied_ids"] else "failed",
            })
        return {**fields, **uncertain, "delivery_status": "applied"}


def apply_tag_edit(item_key: str, req: ZoteroItemTagUpdateRequest,
                   reader: ZoteroReader, writer: ZoteroWriter) -> dict:
    return _apply_edit("direct_tags", item_key, req, reader, writer)


def apply_priority_edit(item_key: str, req: ZoteroItemPriorityUpdateRequest,
                        reader: ZoteroReader, writer: ZoteroWriter) -> dict:
    return _apply_edit("direct_priority", item_key, req, reader, writer)


def apply_collection_edit(item_key: str, req: ZoteroItemCollectionUpdateRequest,
                          reader: ZoteroReader, writer: ZoteroWriter) -> dict:
    return _apply_edit("direct_collections", item_key, req, reader, writer)
