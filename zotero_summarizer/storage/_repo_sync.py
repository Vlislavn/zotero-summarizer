"""SQLite revision log and atomic offline-mutation application."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_USER
from zotero_summarizer.storage.repositories import _connect_to

SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_changes (
    revision    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key   TEXT NOT NULL,
    field      TEXT NOT NULL CHECK (field IN ('verdict', 'review_note')),
    value      TEXT,
    comment    TEXT,
    source     TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sync_changes_item_field
    ON sync_changes(item_key, field, revision);
CREATE TABLE IF NOT EXISTS sync_mutations (
    mutation_id TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    field       TEXT NOT NULL,
    operation   TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    resolves_mutation_id TEXT,
    request_json TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS sync_label_insert AFTER INSERT ON label_verdicts BEGIN
  INSERT INTO sync_changes(item_key, field, value, comment, source)
  VALUES (NEW.item_key, 'verdict', NEW.user_priority, NEW.comment, NEW.source);
END;
CREATE TRIGGER IF NOT EXISTS sync_label_update AFTER UPDATE ON label_verdicts
WHEN OLD.user_priority IS NOT NEW.user_priority
  OR OLD.comment IS NOT NEW.comment OR OLD.source IS NOT NEW.source BEGIN
  INSERT INTO sync_changes(item_key, field, value, comment, source)
  VALUES (NEW.item_key, 'verdict', NEW.user_priority, NEW.comment, NEW.source);
END;
CREATE TRIGGER IF NOT EXISTS sync_label_delete AFTER DELETE ON label_verdicts BEGIN
  INSERT INTO sync_changes(item_key, field, value, comment, source)
  VALUES (OLD.item_key, 'verdict', NULL, NULL, OLD.source);
END;
CREATE TRIGGER IF NOT EXISTS sync_note_insert AFTER INSERT ON review_notes BEGIN
  INSERT INTO sync_changes(item_key, field, value)
  VALUES (NEW.item_key, 'review_note', NEW.note);
END;
CREATE TRIGGER IF NOT EXISTS sync_note_update AFTER UPDATE ON review_notes
WHEN OLD.note IS NOT NEW.note BEGIN
  INSERT INTO sync_changes(item_key, field, value)
  VALUES (NEW.item_key, 'review_note', NEW.note);
END;
CREATE TRIGGER IF NOT EXISTS sync_note_delete AFTER DELETE ON review_notes BEGIN
  INSERT INTO sync_changes(item_key, field, value)
  VALUES (OLD.item_key, 'review_note', NULL);
END;
"""


def apply_sync_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SYNC_SCHEMA)


def _canonical(conn: sqlite3.Connection, item_key: str, field: str) -> dict[str, Any]:
    if field == "verdict":
        row = conn.execute(
            "SELECT user_priority, comment, source, original_derived_priority "
            "FROM label_verdicts WHERE item_key = ?", (item_key,),
        ).fetchone()
        return ({"value": row["user_priority"], "comment": row["comment"],
                 "source": row["source"], "model_priority": row["original_derived_priority"]}
                if row else {"value": None, "comment": None, "source": None,
                             "model_priority": None})
    row = conn.execute(
        "SELECT note FROM review_notes WHERE item_key = ?", (item_key,),
    ).fetchone()
    return {"value": row["note"] if row else None}


def _latest_revision(conn: sqlite3.Connection, item_key: str, field: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM sync_changes "
        "WHERE item_key = ? AND field = ?", (item_key, field),
    ).fetchone()
    return int(row[0])


def _write_value(conn: sqlite3.Connection, request: dict[str, Any]) -> None:
    item_key, field = request["item_key"], request["field"]
    if request["operation"] == "delete":
        table = "label_verdicts" if field == "verdict" else "review_notes"
        conn.execute(f"DELETE FROM {table} WHERE item_key = ?", (item_key,))
        return
    now = datetime.now(timezone.utc).isoformat()
    if field == "verdict":
        conn.execute(
            """INSERT INTO label_verdicts
               (item_key, original_derived_priority, user_priority, comment, created_at, source)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_key) DO UPDATE SET
                 user_priority=excluded.user_priority, comment=excluded.comment,
                 created_at=excluded.created_at, source=excluded.source""",
            (item_key, request.get("model_priority") or "unknown", request["value"],
             request.get("comment") or "", now, VERDICT_SOURCE_USER),
        )
    else:
        conn.execute(
            """INSERT INTO review_notes(item_key, note, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(item_key) DO UPDATE SET
                 note=excluded.note, updated_at=excluded.updated_at""",
            (item_key, request.get("value") or "", now),
        )


def apply_sync_mutation(db_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Idempotently compare-and-write one field under ``BEGIN IMMEDIATE``."""
    public_request = {key: value for key, value in request.items() if not key.startswith("_")}
    request_json = json.dumps(public_request, sort_keys=True, separators=(",", ":"))
    conn = _connect_to(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        seen = conn.execute(
            "SELECT request_json, result_json FROM sync_mutations WHERE mutation_id = ?",
            (request["mutation_id"],),
        ).fetchone()
        if seen:
            if seen["request_json"] != request_json:
                raise ValueError("mutation_id was already used for a different mutation")
            conn.commit()
            stored = json.loads(seen["result_json"])
            if stored["status"] == "conflict":
                return stored
            return {**stored, "status": "already_applied"}

        item_key, field = request["item_key"], request["field"]
        resolves = request.get("resolves_mutation_id")
        if resolves:
            prior = conn.execute(
                "SELECT item_key, field, result_json FROM sync_mutations WHERE mutation_id = ?",
                (resolves,),
            ).fetchone()
            if (prior is None or prior["item_key"] != item_key or prior["field"] != field
                    or json.loads(prior["result_json"]).get("status") != "conflict"):
                raise ValueError("resolves_mutation_id must name a conflict for the same field")
        latest = _latest_revision(conn, item_key, field)
        previous = _canonical(conn, item_key, field)
        if latest > request.get("_effective_base_revision", request["base_revision"]):
            result = {"mutation_id": request["mutation_id"], "status": "conflict",
                      "conflict_revision": latest, "canonical": previous}
        else:
            _write_value(conn, request)
            applied = _latest_revision(conn, item_key, field)
            result = {"mutation_id": request["mutation_id"], "status": "applied",
                      "applied_revision": applied, "previous": previous,
                      "canonical": _canonical(conn, item_key, field)}
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        conn.execute(
            """INSERT INTO sync_mutations
               (mutation_id, device_id, item_key, field, operation, base_revision,
                resolves_mutation_id, request_json, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request["mutation_id"], request["device_id"], item_key, field,
             request["operation"], request["base_revision"],
             request.get("resolves_mutation_id"), request_json, result_json,
             request["created_at"]),
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def pull_sync_changes(db_path: Path, since: int) -> dict[str, Any]:
    conn = _connect_to(db_path)
    try:
        cursor = int(conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM sync_changes"
        ).fetchone()[0])
        rows = conn.execute(
            """SELECT revision, item_key, field, value, comment, source, changed_at
               FROM sync_changes WHERE revision > ? ORDER BY revision""", (since,),
        ).fetchall()
    finally:
        conn.close()
    return {"cursor": cursor, "changes": [dict(row) for row in rows]}


def sync_current_fields(db_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    conn = _connect_to(db_path)
    try:
        conn.execute("BEGIN")
        rows = conn.execute(
            """SELECT item_key, 'verdict' AS field, user_priority AS value,
                      comment, source, original_derived_priority AS model_priority
                      FROM label_verdicts
               UNION ALL
               SELECT item_key, 'review_note', note, NULL, NULL, NULL FROM review_notes"""
        ).fetchall()
        revisions = conn.execute(
            """SELECT item_key, field, MAX(revision) AS revision
               FROM sync_changes GROUP BY item_key, field"""
        ).fetchall()
    finally:
        conn.close()
    fields = {(row["item_key"], row["field"]): dict(row) for row in rows}
    for revision in revisions:
        key = (revision["item_key"], revision["field"])
        row = fields.setdefault(key, {
            "item_key": key[0], "field": key[1], "value": None,
            "comment": None, "source": None, "model_priority": None,
        })
        row["revision"] = int(revision["revision"])
    for row in fields.values():
        row.setdefault("revision", 0)
    return fields


def sync_status(db_path: Path) -> dict[str, int]:
    conn = _connect_to(db_path)
    try:
        cursor = int(conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM sync_changes"
        ).fetchone()[0])
        mutations = int(conn.execute("SELECT COUNT(*) FROM sync_mutations").fetchone()[0])
        conflicts = int(conn.execute(
            "SELECT COUNT(*) FROM sync_mutations WHERE json_extract(result_json, '$.status') = 'conflict'"
        ).fetchone()[0])
    finally:
        conn.close()
    return {"cursor": cursor, "mutations": mutations, "conflicts": conflicts}


__all__ = ["apply_sync_schema", "apply_sync_mutation", "pull_sync_changes",
           "sync_current_fields", "sync_status"]
