"""Presented Pending effects, one durable claim and one target-bound native batch."""
from __future__ import annotations

import json

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models.zotero_actions import PendingApprovalPreview, ZoteroTargetIdentity
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.zotero.generated_notes import validate_generated_changes
from zotero_summarizer.storage import pending_actions
from zotero_summarizer.storage.rows import PendingChangeRow


def _conflict(message: str) -> APIError:
    return APIError(error="pending_write_conflict", message=message, status_code=409)


def _approved(row: dict) -> dict | None:
    return next((event for event in reversed(row["audit"])
                 if event.get("event") == "approved"), None)


def _snapshot(row: dict, target: ZoteroTargetIdentity) -> PendingApprovalPreview:
    generated = row.get("generated_preview")
    payload = row["payload_json"]
    return PendingApprovalPreview(
        change_id=row["id"], item_key=row["item_key"], change_type=row["change_type"],
        payload=json.loads(payload) if isinstance(payload, str) else payload,
        edit_revision=pending_actions.edit_revision(row["audit"]), target=target,
        generated_preview_sha256=generated["preview_sha256"] if generated else None,
    )


def present_changes(rows: list[dict], writer: ZoteroWriter | None) -> list[dict]:
    origins = [pending_actions.history_request(row) for row in rows]
    current = (writer.target_identity() if writer is not None and any(
        origin is None and not row.get("generated_preview") and row["status"] in {"pending", "failed"}
        for row, origin in zip(rows, origins)
    ) else None)
    presented = []
    for row, origin in zip(rows, origins):
        if origin is not None:
            presented.append({**row, "approval_preview": None})
            continue
        generated = row.get("generated_preview")
        approved = _approved(row)
        if generated:
            target = generated["target"]
        elif writer is None:
            target = None
        elif row["status"] in {"pending", "failed"}:
            target = current
        else:
            target = approved["target"] if approved else None
        preview = None
        if target is not None:
            snapshot = _snapshot(row, ZoteroTargetIdentity.model_validate(target, strict=True))
            preview = {**snapshot.model_dump(), "preview_sha256": snapshot.preview_sha256}
        presented.append({**row, "approval_preview": preview})
    return presented


def _read_selected(conn, change_ids: list[int]) -> list[dict]:
    slots = ",".join("?" for _ in change_ids)
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM pending_changes WHERE id IN ({slots})", change_ids)]
    require_pending_actions(rows)
    found = {row["id"]: PendingChangeRow.from_row(row).to_dict() for row in rows}
    return [found[change_id] for change_id in change_ids if change_id in found]


def _validate_selection(rows: list[dict], previews: dict[int, str],
                        target: ZoteroTargetIdentity, source_status: str) -> list[dict]:
    if {row["id"] for row in rows} != set(previews):
        raise _conflict("A selected Pending action disappeared")
    for row in rows:
        generated = row.get("generated_preview")
        if generated and generated["target"] != target.model_dump():
            raise _conflict("Generated action has a different or unknown native target")
        digest = _snapshot(row, target).preview_sha256
        if previews[row["id"]] != digest:
            raise _conflict("Pending preview changed; review and select it again")
        if row["status"] not in {source_status, "applied"}:
            raise _conflict("Pending state changed; uncertain delivery needs reconciliation")
        if row["status"] == "applied" and not generated:
            approved = _approved(row)
            if not approved or approved.get("preview_sha256") != digest:
                raise _conflict("Applied action has no matching approval history")
    validate_generated_changes(
        [row for row in rows if row.get("generated_preview")], target, source_status,
    )
    return [row for row in rows if row["status"] != "applied"]


def require_pending_actions(rows: list[dict]) -> None:
    try:
        pending_actions.ensure_pending(rows)
    except (ValueError, TypeError, KeyError) as exc:
        raise _conflict("Direct, mirror or malformed history cannot authorize Pending actions") from exc


def check_uncertainty(conn, item_keys: list[str], target: ZoteroTargetIdentity, *,
                      owned_claim: dict | None = None) -> None:
    blockers = pending_actions.applying_blockers(conn, item_keys, target, owned_claim=owned_claim)
    if blockers:
        raise APIError(error="zotero_write_uncertain", message="Earlier delivery needs reconciliation",
                       status_code=409, details={"delivery_status": "uncertain", "blocking_action_ids": blockers})


def _claim(conn, ready: list[dict], previews: dict[int, str],
           backup: str, target: ZoteroTargetIdentity) -> None:
    for row in ready:
        generated = row.get("generated_preview")
        pending_actions.record(conn, row["id"], {
            "event": "approved", "actor": "current_user", "source": "pending_confirmation",
            "preview_sha256": previews[row["id"]],
            "edit_revision": pending_actions.edit_revision(row["audit"]),
            "target": target.model_dump(),
            "generated_preview_sha256": generated["preview_sha256"] if generated else None,
            "recovery": "backup_only", "backup_path": backup,
        }, status="applying", backup_path=backup)


def _finalize(conn, claimed: dict[int, dict], result: dict) -> None:
    applied = result["applied_ids"]
    failures = result["failed"]
    failed_ids = [failure["id"] for failure in failures]
    ids = [*applied, *failed_ids]
    if (any(type(change_id) is not int for change_id in ids) or
            len(ids) != len(set(ids)) or set(ids) != set(claimed)):
        raise ValueError("Pending writer returned an incomplete or contradictory outcome")
    conn.execute("BEGIN IMMEDIATE")
    rows = _read_selected(conn, list(claimed))
    if len(rows) != len(claimed):
        raise ValueError("Claimed Pending action disappeared")
    for row in rows:
        expected = claimed[row["id"]]
        approved = _approved(row)
        target = ZoteroTargetIdentity.model_validate(expected["target"], strict=True)
        if (row["status"] != "applying" or not approved or
                approved.get("preview_sha256") != expected["preview_sha256"] or
                approved.get("target") != expected["target"] or
                approved.get("backup_path") != expected["backup_path"] or
                row["backup_path"] != expected["backup_path"] or
                _snapshot(row, target).preview_sha256 != expected["preview_sha256"]):
            raise ValueError("Pending claim changed before finalization")
    errors = {failure["id"]: failure["error"] for failure in failures}
    for row in rows:
        status = "failed" if row["id"] in errors else "applied"
        pending_actions.record(conn, row["id"], {
            "event": status, "actor": "system",
            "source": "approved_generated_note" if row.get("generated_preview") else "approved_pending_change",
        }, status=status, error=errors.get(row["id"], ""))


def apply_confirmed_changes(change_ids: list[int], previews: dict[int, str],
                            writer: ZoteroWriter, source_status: str, force: bool = False) -> dict:
    if not change_ids or len(change_ids) != len(set(change_ids)) or set(change_ids) != set(previews):
        raise APIError(error="validation_error", message="Selected IDs require exact previews", status_code=422)
    with pending_actions.locked_queue() as advisory_conn:
        advisory = _read_selected(advisory_conn, change_ids)
    has_generated = any(row.get("generated_preview") for row in advisory)
    with _review_cache.review_publication_lock(require_publication=has_generated), pending_actions.locked_queue() as conn:
        rows = _read_selected(conn, change_ids)
        if not has_generated and any(row.get("generated_preview") for row in rows):
            raise _conflict("Pending family changed; review the selection again")
        target = writer.target_identity()
        ready = _validate_selection(rows, previews, target, source_status)
        check_uncertainty(conn, list(dict.fromkeys(row["item_key"] for row in rows)), target)
        if not ready:
            return {"applied_ids": [], "failed": [], "backup_path": None, "ordinary_applied_item_keys": []}
        if writer.is_connector_running() and not force:
            return {"error": "zotero_running", "requires_force": True,
                    "message": "Zotero appears to be running; close Zotero or confirm force apply."}
        backup = writer.backup_database(expected_target=target)
        if not backup:
            raise _conflict("Pending writes require a retained backup reference")
        _claim(conn, ready, previews, backup, target)
        claimed = {row["id"]: {"preview_sha256": previews[row["id"]],
                              "target": target.model_dump(), "backup_path": backup} for row in ready}
        conn.commit()  # durable uncertainty precedes the separate native transaction
        result = writer.apply_changes(ready, False, precreated_backup=backup, expected_target=target)
        if result["backup_path"] != backup:
            raise ValueError("Pending writer returned a different backup reference")
        _finalize(conn, claimed, result)
        return {**result, "ordinary_applied_item_keys": list(dict.fromkeys(
            row["item_key"] for row in ready
            if row["id"] in result["applied_ids"] and not row.get("generated_preview")
        ))}
