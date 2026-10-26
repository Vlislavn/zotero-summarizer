"""Current app mirrors: committed native history, then explicit domain ACK."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations._zotero_write_tags import _native_note_html, _owns_note
from zotero_summarizer.integrations.zotero_read import ZoteroReadError
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.models.zotero_actions import (
    CompositeNoteIntent, SystemMirrorRequest, UserNotePayload, VerdictMirrorIntent, VerdictNotePayload,
    VERDICT_NOTE_MARKER,
)
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.zotero._notes import build_user_note_html
from zotero_summarizer.services.zotero.pending import build_label_tag_change
from zotero_summarizer.services.zotero.pending_confirmation import check_uncertainty
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.storage import label_mirrors, pending_actions, review_note_projection


def _optional_io(exc: BaseException) -> bool:
    if isinstance(exc, (ZoteroReadError, ZoteroWriteError)):
        return exc.expected_io is True
    if isinstance(exc, OSError):
        return True
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", None)
        return code == sqlite3.SQLITE_CONSTRAINT_TRIGGER or (
            code is not None and code & 255 in {
                sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR,
                sqlite3.SQLITE_FULL, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN,
            }
        )
    return False


def _signature(value: dict) -> tuple:
    return tuple(value[field] for field in ("revision", "body", "target_key"))


class _MirrorDelivery:
    source = "mirror_user_note"

    def __init__(self, conn, desired: dict, receipt: dict):
        self.conn, self.desired, self.receipt = conn, desired, receipt
        self.reader = zotero.get_zotero_reader_or_raise()
        self.writer = zotero.get_zotero_writer_or_raise()
        self.target = self.writer.target_identity()
        self.intent = self._intent(desired)
        self.actions = self._actions(desired)

    def _intent(self, desired):
        return CompositeNoteIntent(lookup_item_key=desired["item_key"], revision=desired["revision"],
                                   body=desired["body"], target_key=desired["target_key"])

    def _actions(self, desired):
        payload = UserNotePayload(note_html=build_user_note_html(desired["body"]))
        return [{"change_type": "upsert_note", "payload_json": payload.model_dump()}]

    def _snapshot(self):
        return review_note_projection.snapshot(self.conn, self.intent.lookup_item_key)

    def _domain_ack(self):
        return review_note_projection.acknowledge(self.conn, self.desired)

    def _admit(self, claimed: list[dict] | None = None) -> None:
        check_uncertainty(self.conn, [self.intent.target_key], self.target, owned_claims=claimed)

    def _reader_matches(self) -> bool:
        path = self.reader.db_path.resolve(strict=True)
        identity = path.stat()
        return (path == Path(self.target.database_path) and
                (identity.st_dev, identity.st_ino) == (self.target.device, self.target.inode))

    def _refuse(self, reason: str) -> None:
        raise APIError(error=reason, message="System mirror cannot deliver this bound intent", status_code=409)

    def _validate(self) -> str | None:
        current = self._snapshot()
        if current is None or self._intent(current) != self.intent:
            return "projection_changed"
        try:
            if self.writer.target_identity() != self.target or not self._reader_matches():
                return "target_changed"
        except OSError:
            return "target_changed"
        return None

    def _reuse(self) -> list[dict]:
        reusable = []
        for row, origin in pending_actions.mirror_receipts(self.conn, self.intent.target_key):
            if origin["source"] != self.source or _signature(origin["requested"]) != _signature(self.desired):
                continue
            if origin["target"] != self.target.model_dump():
                self._refuse("target_changed")
            if (row["change_type"] != self.actions[0]["change_type"] or
                    json.loads(row["payload_json"]) != self.actions[0]["payload_json"]):
                raise ValueError("Stored mirror payload differs from its current complete intent")
            current = review_note_projection.snapshot(self.conn, origin["requested"]["lookup_item_key"])
            if _signature(current) != _signature(self.desired):
                self._refuse("projection_changed")
            if pending_actions.mirror_state(row) == "applied":
                reusable = [row]
        return reusable

    def _commit(self, error: str | None = None, *, domain_ack: bool = False) -> bool:
        try:
            self.conn.commit()
        except (sqlite3.Error, OSError) as exc:
            if not _optional_io(exc):
                raise
            ack_unknown = domain_ack and not self.conn.in_transaction
            self.conn.rollback()
            self.receipt["note_error"] = error or type(exc).__name__
            if ack_unknown:
                self.receipt["note_pending"] = None
            return False
        return True

    def _predispatch(self, claimed: list[dict]) -> bool:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            for row in claimed:
                pending_actions.ensure_claim(self.conn, row)
            reason = self._validate()
            if reason is None and self._actions(self._snapshot()) != self.actions:
                reason = "projection_changed"
            if reason is None and self.writer.is_connector_running():
                reason = "ZoteroWriteError"
            if reason is not None:
                for row in claimed:
                    pending_actions.abort_mirror(self.conn, row, reason)
                self.receipt["note_error"] = reason
                self._commit("receipt_unconfirmed")
                return False
            self._admit(claimed)
        except (sqlite3.Error, OSError) as exc:
            if not _optional_io(exc):
                raise
            self.conn.rollback()
            self.receipt["note_error"] = "receipt_unconfirmed"
            return False
        return True

    def _write(self, title: str) -> list[dict]:
        if self.writer.is_connector_running():
            raise ZoteroWriteError("Zotero is open; close it to save changes.", expected_io=True)
        backup = self.writer.backup_database(expected_target=self.target)
        request = SystemMirrorRequest(source=self.source, requested=self.intent, target=self.target, backup_path=backup)
        try:
            claimed = pending_actions.claim_mirror(self.conn, title, request, self.actions)
        except (sqlite3.Error, OSError) as exc:
            if not _optional_io(exc):
                raise
            self.conn.rollback()
            self.receipt["note_error"] = "receipt_unconfirmed"
            return []
        if not self._commit("receipt_unconfirmed") or not self._predispatch(claimed):
            return []
        try:
            result = self.writer.apply_changes(claimed, False, precreated_backup=backup, expected_target=self.target)
        except (ZoteroWriteError, sqlite3.Error, OSError) as exc:
            if not _optional_io(exc):
                raise
            self.conn.rollback()
            self.receipt["note_error"] = "zotero_write_uncertain"
            return []
        failures = pending_actions.native_partition(claimed, result)
        self.receipt["note_written"] = not failures
        try:
            pending_actions.finalize_claims(self.conn, claimed, failures)
        except (sqlite3.Error, OSError) as exc:
            if not _optional_io(exc):
                raise
            self.conn.rollback()
            self.receipt["note_error"] = "zotero_write_uncertain"
            return []
        if not self._commit("zotero_write_uncertain"):
            return []
        if failures:
            failure = next(iter(failures.values()))
            raise ZoteroWriteError(failure["error"], expected_io=failure["expected_io"])
        return [pending_actions.history_row(self.conn, row["id"]) for row in claimed]

    def _acknowledge(self, rows: list[dict]) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        reason = self._validate()
        if reason is not None:
            self.receipt["note_error"] = reason
            self.conn.rollback()
            return
        self._admit()
        for row in rows:
            if pending_actions.history_row(self.conn, row["id"]) != row:
                raise ValueError("Applied mirror receipt changed before domain ACK")
            pending_actions.require_applied_mirror(row)
        if not self._domain_ack():
            self.receipt["note_error"] = "projection_changed"
            self.conn.rollback()
            return
        for row in rows:
            pending_actions.acknowledge_mirror(self.conn, row)
        if self._commit(domain_ack=True):
            self.receipt["note_pending"] = False

    def deliver(self) -> None:
        self._admit()
        if not self._reader_matches():
            self._refuse("target_changed")
        rows = self._reuse()
        detail = self.reader.get_item_detail(self.intent.target_key)
        if detail is None:
            self.receipt["note_error"] = "target_unavailable"
            return
        if not rows and self.actions:
            rows = self._write(str(detail.get("title") or ""))
        elif not self._commit():
            return
        if rows or not self.actions:
            self._acknowledge(rows)


class _VerdictDelivery(_MirrorDelivery):
    source = "mirror_verdict"

    def _intent(self, desired):
        return VerdictMirrorIntent(lookup_item_key=desired["lookup_item_key"], source_item_key=desired["item_key"],
                                   revision=desired["revision"], value=desired["value"], comment=desired["comment"],
                                   verdict_source=desired["source"], target_key=desired["target_key"])

    def _snapshot(self):
        return label_mirrors.snapshot(self.conn, self.intent.lookup_item_key,
                                      redeliver=self.desired["label_pending"])

    def _actions(self, desired):
        key, priority = desired["target_key"], desired["value"]
        comment = (desired["comment"] or "") if priority is not None else ""
        detail = self.reader.get_item_detail(key)
        if detail is None:
            raise ZoteroWriteError(f"item {key!r} not found in Zotero")
        owned = [note for note in self.reader.get_item_notes(key) if _owns_note(note["note"], VERDICT_NOTE_MARKER)]
        self.note_required = bool(comment.strip()) or bool(owned)
        actions = []
        if self.note_required:
            payload = VerdictNotePayload.for_intent(self._intent(desired))
            if not owned or any(note["note"] != _native_note_html(payload.note_html) for note in owned):
                actions.append({"change_type": "upsert_note", "payload_json": payload.model_dump()})
        if desired["label_pending"]:
            payload = build_label_tag_change(detail["tags"], priority)
            if payload["add_tags"] or payload["remove_tags"]:
                actions.append({"change_type": "tag_changes", "payload_json": payload})
        return actions

    def _reuse(self):
        for row, _origin in pending_actions.mirror_receipts(self.conn, self.intent.target_key):
            pending_actions.mirror_state(row)
        return []

    def _domain_ack(self):
        if self._actions(self._snapshot()):
            return False
        return label_mirrors.acknowledge(self.conn, self.desired)


def mirror_verdict(db_path: Path, item_key: str, *, redeliver: bool = False) -> dict | None:
    with _review_cache.review_publication_lock(require_publication=False):
        with label_mirrors.current_label(db_path, item_key, redeliver=redeliver) as (conn, desired):
            if desired is None:
                return None
            receipt = {"note_error": None, "note_written": False}
            delivery = _VerdictDelivery(conn, desired, receipt)
            delivery.deliver()
            if receipt["note_error"] is not None:
                raise APIError(receipt["note_error"], "Verdict mirror delivery was not confirmed", status_code=409)
            return {"label_written": desired["label_pending"], "note_written": delivery.note_required}


def mirror_review_note(db_path: Path, item_key: str) -> dict[str, Any]:
    receipt = {"note_written": False, "note_error": None, "note_pending": None,
               "projection_revision": None, "target_key": None}
    try:
        with _review_cache.review_publication_lock(require_publication=False):
            with review_note_projection.current_for_mirror(db_path, item_key) as (conn, desired):
                receipt.update(projection_revision=desired["revision"], target_key=desired["target_key"],
                               note_pending=desired["pending"])
                if desired["pending"]:
                    if desired["target_key"] is None:
                        receipt["note_error"] = "target_unavailable"
                    else:
                        _MirrorDelivery(conn, desired, receipt).deliver()
    except APIError as exc:
        if exc.error not in {"zotero_unavailable", "zotero_write_uncertain", "target_changed", "projection_changed"}:
            raise
        receipt["note_error"] = exc.error
    except (ZoteroReadError, ZoteroWriteError, sqlite3.Error, OSError) as exc:
        if not _optional_io(exc):
            raise
        receipt["note_error"] = type(exc).__name__
    return receipt
