"""Revision/claim audit and generated metadata on the existing Pending queue."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from zotero_summarizer.domain import LABEL_TAG_CASEFOLDED, label_tag_for_priority
from zotero_summarizer.models.api import (
    ZoteroItemCollectionUpdateRequest, ZoteroItemPriorityUpdateRequest, ZoteroItemTagUpdateRequest,
)
from zotero_summarizer.models.zotero_actions import (
    GeneratedNoteProposal, MirrorNoteRequest, UserNotePayload, ZoteroTargetIdentity,
)
from zotero_summarizer.storage import repositories


def apply_schema(conn) -> None:
    for name, declaration in (
        ("proposal_json", "TEXT"), ("audit_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("backup_path", "TEXT"),
    ):
        if name not in repositories.table_columns(conn, "pending_changes"):
            conn.execute(f"ALTER TABLE pending_changes ADD COLUMN {name} {declaration}")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS pending_generated_immutable
        BEFORE UPDATE ON pending_changes WHEN OLD.proposal_json IS NOT NULL AND (
            NEW.id IS NOT OLD.id OR NEW.item_key IS NOT OLD.item_key OR
            NEW.change_type IS NOT OLD.change_type OR NEW.payload_json IS NOT OLD.payload_json OR
            NEW.proposal_json IS NOT OLD.proposal_json)
        BEGIN SELECT RAISE(ABORT, 'generated proposals are immutable'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS pending_generated_no_delete
        BEFORE DELETE ON pending_changes WHEN OLD.proposal_json IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'generated proposals retain audit history'); END""")


@contextmanager
def locked_queue() -> Iterator:
    """Lock order: review publication lock, then this queue transaction."""
    conn = repositories._get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def edit_revision(events: list[dict]) -> int:
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Invalid pending audit")
    return sum(event.get("event") == "payload_edited" for event in events)


def record(conn, change_id: int, event: dict, *, status: str | None = None,
           backup_path: str | None = None, error: str = "") -> None:
    row = conn.execute("SELECT audit_json FROM pending_changes WHERE id=?", (change_id,)).fetchone()
    events = json.loads(row["audit_json"])
    timestamp = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
    events.append({**event, "at": timestamp})
    conn.execute("""UPDATE pending_changes SET audit_json=?, status=COALESCE(?,status),
        backup_path=COALESCE(?,backup_path), error_message=?,
        applied_at=CASE WHEN ?='applied' THEN datetime('now') ELSE applied_at END WHERE id=?""",
        (json.dumps(events, ensure_ascii=False), status, backup_path, error, status, change_id))


def stage(item_title: str, proposal: GeneratedNoteProposal) -> int:
    serialized = proposal.model_dump_json()
    with locked_queue() as conn:
        existing = conn.execute(
            "SELECT id FROM pending_changes WHERE proposal_json=? LIMIT 1", (serialized,),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cursor = conn.execute("""INSERT INTO pending_changes
            (item_key,item_title,change_type,payload_json,proposal_json) VALUES (?,?,?,?,?)""",
            (proposal.item_key, item_title, proposal.change_type,
             proposal.payload.model_dump_json(), serialized))
        change_id = int(cursor.lastrowid)
        record(conn, change_id, {"event": "staged", "actor": "system", "source": proposal.source,
                                 "preview_sha256": proposal.preview_sha256})
        return change_id


def reject_generated(change_ids: list[int]) -> int:
    with locked_queue() as conn:
        slots = ",".join("?" for _ in change_ids)
        ensure_pending(conn.execute(
            f"SELECT * FROM pending_changes WHERE id IN ({slots})", change_ids,
        ))
        rows = conn.execute(
            f"SELECT id FROM pending_changes WHERE id IN ({slots})"
            " AND proposal_json IS NOT NULL AND status='pending'", change_ids,
        ).fetchall()
        for row in rows:
            record(conn, row["id"], {"event": "rejected", "actor": "current_user",
                                    "source": "pending_rejection"}, status="rejected")
        return len(rows)


def preview(row: dict) -> dict | None:
    serialized = row.get("proposal_json")
    if serialized is None:
        return None
    proposal = GeneratedNoteProposal.model_validate_json(serialized)
    if (row["item_key"] != proposal.item_key or row["change_type"] != proposal.change_type or
            json.loads(row["payload_json"]) != proposal.payload.model_dump()):
        raise ValueError("Stored generated action differs from its immutable preview")
    return {**proposal.model_dump(), "preview_sha256": proposal.preview_sha256}


_DIRECT_REQUESTS = {
    "direct_tags": ZoteroItemTagUpdateRequest,
    "direct_priority": ZoteroItemPriorityUpdateRequest,
    "direct_collections": ZoteroItemCollectionUpdateRequest,
}


def _audit(row: dict) -> list[dict]:
    events = row["audit"] if "audit" in row else json.loads(row["audit_json"])
    edit_revision(events)
    return events


def _requested(source: str, value: dict) -> dict:
    if not isinstance(source, str) or source not in _DIRECT_REQUESTS or not isinstance(value, dict):
        raise ValueError("Invalid direct request origin or request")
    request = _DIRECT_REQUESTS[source].model_validate(value, strict=True)
    if source == "direct_collections":
        normalized = {"add": [ref.to_writer_payload() for ref in request.add],
                      "remove": [ref.to_writer_payload() for ref in request.remove]}
    else:
        normalized = request.model_dump(exclude={"force"})
    if normalized != value:
        raise ValueError("Stored direct request is not the normalized typed request")
    return normalized


def direct_request(row: dict) -> dict | None:
    """Recognize declared direct history before granting any ordinary authority."""
    events = _audit(row)
    declared = any(str(event.get("event", "")).startswith("direct_") or
                   str(event.get("source", "")).startswith("direct_") for event in events)
    if not declared:
        return None
    first = events[0]
    if (first.get("event") != "direct_requested" or first.get("actor") != "current_user" or
            row.get("proposal_json") is not None or type(row.get("id")) is not int or row["id"] <= 0 or
            not isinstance(row.get("item_key"), str) or not row["item_key"].strip() or
            not isinstance(first.get("at"), str) or not first["at"] or
            first.get("recovery") != "backup_only"):
        raise ValueError("Invalid direct creation declaration")
    source = first.get("source")
    requested = _requested(source, first.get("requested"))
    target = ZoteroTargetIdentity.model_validate(first.get("target"), strict=True)
    backup = first.get("backup_path")
    if (not Path(target.database_path).is_absolute() or not isinstance(backup, str) or not backup or
            not Path(backup).is_absolute() or backup != row.get("backup_path")):
        raise ValueError("Invalid direct target or backup reference")
    payload = row["payload_json"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    if source == "direct_collections":
        direction = {"add_to_collection": "add", "remove_from_collection": "remove"}.get(row["change_type"])
        if direction is None or payload not in requested[direction]:
            raise ValueError("Direct membership differs from its request")
    elif row["change_type"] != "tag_changes" or _requested("direct_tags", payload) != payload:
        raise ValueError("Invalid direct tag effect")
    elif source == "direct_tags" and any(
        tag not in requested[field] for field in ("add_tags", "remove_tags") for tag in payload[field]
    ):
        raise ValueError("Direct tag effect differs from its request")
    elif source == "direct_priority":
        label = label_tag_for_priority(requested["priority"])
        if payload["add_tags"] not in ([], [label]) or any(
            tag.casefold() not in LABEL_TAG_CASEFOLDED or tag.casefold() == label.casefold()
            for tag in payload["remove_tags"]
        ):
            raise ValueError("Direct priority effect differs from its request")
    if source != "direct_collections" and (
        {tag.casefold() for tag in payload["add_tags"]} & {tag.casefold() for tag in payload["remove_tags"]}
    ):
        raise ValueError("Direct tag effect is not a normalized delta")
    if any(event.get("event") not in {"direct_applied", "direct_failed", "direct_uncertain"} or
           event.get("actor") != "system" or event.get("source") != source for event in events[1:]):
        raise ValueError("Invalid later direct audit declaration")
    return first


def _mirror_events(events: list[dict]) -> None:
    names = [event.get("event") for event in events]
    if names not in (
        ["mirror_requested"], ["mirror_requested", "mirror_applied"],
        ["mirror_requested", "mirror_applied", "mirror_acknowledged"],
        ["mirror_requested", "mirror_failed"], ["mirror_requested", "mirror_aborted"],
    ):
        raise ValueError("Invalid mirror audit sequence")
    for event in events:
        if (event.get("actor") != "system" or event.get("source") != "mirror_user_note" or
                not isinstance(event.get("at"), str) or not event["at"]):
            raise ValueError("Invalid mirror audit identity")
    for event in events[1:]:
        fields = {"event", "actor", "source", "at"}
        if event["event"] == "mirror_failed":
            fields.add("expected_io")
            if type(event.get("expected_io")) is not bool:
                raise ValueError("Invalid mirror native failure classification")
        if event["event"] == "mirror_aborted":
            fields.update({"delivery_status", "reason"})
            if (event.get("delivery_status") != "not_dispatched" or
                    event.get("reason") not in {"projection_changed", "target_changed", "ZoteroWriteError"}):
                raise ValueError("Invalid mirror predispatch abort")
        if set(event) != fields:
            raise ValueError("Invalid mirror terminal audit fields")


def mirror_request(row: dict) -> dict | None:
    events = _audit(row)
    if not any(str(event.get("event", "")).startswith("mirror_") or
               str(event.get("source", "")).startswith("mirror_") for event in events):
        return None
    first = {key: value for key, value in events[0].items() if key != "at"}
    request = MirrorNoteRequest.model_validate(first, strict=True)
    payload = row["payload_json"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    if (first != request.model_dump() or row.get("proposal_json") is not None or
            type(row.get("id")) is not int or row["id"] <= 0 or
            row["item_key"] != request.requested.target_key or row["change_type"] != "upsert_note" or
            row.get("backup_path") != request.backup_path or
            UserNotePayload.model_validate(payload, strict=True).model_dump() != payload):
        raise ValueError("Invalid mirror creation declaration or effect")
    _mirror_events(events)
    return events[0]


def history_request(row: dict) -> dict | None:
    """Only the closed direct-human or composite system-mirror families are history."""
    return direct_request(row) or mirror_request(row)


def ensure_pending(rows) -> None:
    """Prevalidate a whole selection before any ordinary/generated mutation."""
    for row in rows:
        if history_request(dict(row)) is not None:
            raise ValueError("Direct and mirror receipts are history only, not Pending actions")


def applying_blockers(conn, item_keys: list[str], target: ZoteroTargetIdentity, *,
                      owned_claim: dict | None = None) -> list[int]:
    """Uncapped item selection; exclude only this owner's full unchanged mirror claim."""
    slots = ",".join("?" for _ in item_keys)
    rows = conn.execute(f"SELECT * FROM pending_changes WHERE status='applying' "
                        f"AND item_key IN ({slots}) ORDER BY id", item_keys)
    blocked = []
    for raw in rows:
        row = dict(raw)
        if owned_claim is not None and row["id"] == owned_claim["id"]:
            if row != owned_claim or owned_claim["status"] != "applying" or mirror_request(row) is None:
                raise ValueError("Owned mirror claim changed before admission")
            continue
        try:
            request = history_request(row)
            approved = request or next((event for event in reversed(_audit(row))
                                        if event.get("event") == "approved"), {})
            recorded = ZoteroTargetIdentity.model_validate(approved.get("target"), strict=True)
            if not Path(recorded.database_path).is_absolute():
                raise ValueError("Unknown applying target")
        except (ValueError, TypeError, KeyError):
            blocked.append(row["id"])
            continue
        if recorded == target:
            blocked.append(row["id"])
    return blocked


def history_row(conn, change_id: int) -> dict:
    row = conn.execute("SELECT * FROM pending_changes WHERE id=?", (change_id,)).fetchone()
    if row is None:
        raise ValueError("Claimed history disappeared")
    return dict(row)


def ensure_claim(conn, expected: dict) -> dict:
    actual = history_row(conn, expected["id"])
    if actual != expected or expected["status"] != "applying":
        raise ValueError("Direct claim changed before finalization")
    origin = history_request(actual)
    if origin is None:
        raise ValueError("Applying claim lacks a closed history origin")
    return origin


def _insert_claims(conn, item_key: str, item_title: str, actions: list[dict], request: dict) -> list[dict]:
    claimed = []
    for action in actions:
        cursor = conn.execute("""INSERT INTO pending_changes
            (item_key,item_title,change_type,payload_json,status) VALUES (?,?,?,?,'applying')""",
            (item_key, item_title, action["change_type"], json.dumps(action["payload_json"], ensure_ascii=False)))
        change_id = int(cursor.lastrowid)
        record(conn, change_id, request, status="applying", backup_path=request["backup_path"])
        row = history_row(conn, change_id)
        if history_request(row) is None:
            raise ValueError("A history claim requires a closed origin")
        claimed.append(row)
    return claimed


def claim_direct(conn, item_key: str, item_title: str, actions: list[dict], request: dict) -> list[dict]:
    """Insert positive-ID applying receipts and first origin on the caller's transaction."""
    claimed = _insert_claims(conn, item_key, item_title, actions, request)
    if any(direct_request(row) is None for row in claimed):
        raise ValueError("Direct claims require a direct-human request")
    return claimed


def claim_mirror(conn, item_title: str, request: MirrorNoteRequest, payload: UserNotePayload) -> dict:
    return _insert_claims(conn, request.requested.target_key, item_title, [
        {"change_type": "upsert_note", "payload_json": payload.model_dump()},
    ], request.model_dump())[0]


def native_partition(claimed: list[dict], result: dict) -> dict[int, dict]:
    """Validate complete native IDs, backup equality and explicit failure classification."""
    applied, failed = result["applied_ids"], result["failed"]
    if not isinstance(applied, list) or not isinstance(failed, list):
        raise ValueError("Invalid direct native partition")
    if any(not isinstance(failure, dict) or type(failure.get("expected_io")) is not bool or
           not isinstance(failure.get("error"), str) or not failure["error"] for failure in failed):
        raise ValueError("Invalid direct native failure classification")
    ids = [*applied, *(failure["id"] for failure in failed)]
    if (any(type(change_id) is not int for change_id in ids) or len(ids) != len(set(ids)) or
            set(ids) != {row["id"] for row in claimed} or
            any(result["backup_path"] != row["backup_path"] for row in claimed)):
        raise ValueError("Incomplete or contradictory direct native outcome")
    return {failure["id"]: failure for failure in failed}


def finalize_claims(conn, claimed: list[dict], failures: dict[int, dict]) -> None:
    """Persist a validated partition only after checking every full unchanged claim."""
    if not conn.in_transaction:
        raise ValueError("History finalization requires its caller's transaction")
    origins = [ensure_claim(conn, row) for row in claimed]
    for row, origin in zip(claimed, origins):
        failure = failures.get(row["id"])
        status = "failed" if failure else "applied"
        family = "direct" if origin["event"] == "direct_requested" else "mirror"
        event = {"event": f"{family}_{status}", "actor": "system", "source": origin["source"]}
        if failure:
            event["expected_io"] = failure["expected_io"]
        record(conn, row["id"], event, status=status, error=failure["error"] if failure else "")


def finalize_direct(conn, claimed: list[dict], result: dict) -> None:
    """Validate the full outcome and every unchanged claim before any terminal update."""
    failures = native_partition(claimed, result)
    conn.execute("BEGIN IMMEDIATE")
    finalize_claims(conn, claimed, failures)


def abort_mirror(conn, claimed: dict, reason: str) -> None:
    origin = ensure_claim(conn, claimed)
    if origin["event"] != "mirror_requested" or reason not in {
        "projection_changed", "target_changed", "ZoteroWriteError",
    }:
        raise ValueError("Invalid mirror abort")
    record(conn, claimed["id"], {"event": "mirror_aborted", "actor": "system",
                               "source": origin["source"], "delivery_status": "not_dispatched",
                               "reason": reason}, status="failed", error=reason)


def mirror_state(row: dict) -> str:
    if mirror_request(row) is None:
        raise ValueError("Missing mirror declaration")
    last = _audit(row)[-1]["event"]
    expected = ("applying" if last == "mirror_requested" else
                "failed" if last in {"mirror_failed", "mirror_aborted"} else "applied")
    if row["status"] != expected:
        raise ValueError("Mirror status differs from its audit")
    if expected == "applied":
        if row["error_message"] != "" or not isinstance(row["applied_at"], str) or not row["applied_at"]:
            raise ValueError("Mirror success lacks terminal metadata")
    elif row["applied_at"] is not None or (
        expected == "failed" and (not isinstance(row["error_message"], str) or not row["error_message"])
    ):
        raise ValueError("Invalid non-applied mirror metadata")
    return expected


def require_applied_mirror(row: dict) -> None:
    if mirror_state(row) != "applied":
        raise ValueError("Mirror history does not prove committed native success")


def acknowledge_mirror(conn, expected: dict) -> None:
    actual = history_row(conn, expected["id"])
    if actual != expected:
        raise ValueError("Applied mirror receipt changed before acknowledgement")
    require_applied_mirror(actual)
    if _audit(actual)[-1]["event"] != "mirror_acknowledged":
        record(conn, actual["id"], {"event": "mirror_acknowledged", "actor": "system",
                                  "source": "mirror_user_note"})


def mirror_receipts(conn, item_key: str) -> Iterator[tuple[dict, dict]]:
    for raw in conn.execute("SELECT * FROM pending_changes WHERE item_key=? ORDER BY id", (item_key,)):
        row = dict(raw)
        origin = history_request(row)
        if origin is not None and origin["event"] == "mirror_requested":
            yield row, origin
