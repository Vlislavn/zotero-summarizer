"""Phase 1.14 — review-mode service layer.

Operations against ``processed_feed_items`` rows whose ``decision`` is
``awaiting_review``. The web UI calls these via :mod:`api.routes.review`.

Single responsibility: state transitions for review-mode items + golden CSV
append. No HTTP concerns (those live in the route module).
"""
from __future__ import annotations

import json as _json
import sqlite3
from dataclasses import asdict
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_USER
from zotero_summarizer.services import interaction_log
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden.goldenset import _PRIORITY_TO_RELEVANCE
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.services.library.review_summary import prepare_training_sample
from zotero_summarizer.services.triage.daily_select._candidate import parse_payload
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.feed_identity import row_feed_keys

# ---------------------------------------------------------------------------
# Connection helper (mirrors feeds.py)
# ---------------------------------------------------------------------------


def _conn():
    return feeds_storage.open_triage_conn(get_settings().triage_db_path)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_by_state(
    state: str, since_hours: int = 720, limit: int = 1000, *, sort: str = "recent",
) -> list[dict[str, Any]]:
    """Return ranked, limited rows with ``decision == state`` and parsed payload.

    The review UI uses this for both ``awaiting_review`` (the LLM-or-gate-only
    triage queue) and ``gate_rejected`` (items the classifier dropped before
    LLM — exposed so the user can spot-check false negatives and relabel).

    Trashed re-arrivals are suppressed by stable GUID (the same guard the Today
    slate applies) so a paper the user already threw away never reappears here.
    """
    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[state],
            since_hours=since_hours,
            limit=limit,
            sort=sort,
        )
        rows = _drop_trashed_rearrivals(conn, rows)
    return [_decorate_row(r) for r in rows]


def _decorate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON payload column into structured fields for the UI."""
    out = dict(row)
    payload = parse_payload(row)
    out["shap"] = payload.get("shap")
    out["aux_context"] = payload.get("aux_context")
    out["summary"] = payload.get("summary")
    out["audit_pick"] = bool(payload.get("audit_pick"))   # Phase 1.15 (2.3)
    return out


def _fetch_row(conn: sqlite3.Connection, processed_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM processed_feed_items WHERE id = ?",
        (int(processed_id),),
    ).fetchone()
    if row is None:
        raise KeyError(f"processed_feed_items id={processed_id} not found")
    return dict(row)


def _log_review(row: dict[str, Any], surface: str, value: str) -> None:
    """Append the review-UI decision (model's prediction + the human's label).

    ``row`` carries the gate's prediction unmutated (the DB write changes the
    column, not this dict), so the model block records what the human overrode.
    """
    interaction_log.log_feed_decision(
        row=row, item_key=row_feed_keys(row)[0],
        surface=surface, human={"kind": "priority", "value": value},
    )


def _priority_for_positive_review(row: dict[str, Any]) -> str:
    priority = str(row.get("reading_priority") or "").strip()
    if priority in _PRIORITY_TO_RELEVANCE and priority != "dont_read":
        return priority
    return "should_read"


def approve(processed_id: int) -> dict[str, Any]:
    """Approve and prepare a trainable row; Zotero writes remain in Apply-all."""
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
    _require_state(row, feeds_storage.DECISION_AWAITING_REVIEW)
    _unpack_summary(row)
    return _commit_review(row, _priority_for_positive_review(row), surface="review_approve")


def reject(processed_id: int, *, write_to_golden: bool = True) -> dict[str, Any]:
    """Reject an awaiting row, optionally without adding a training example."""
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
    _require_state(row, feeds_storage.DECISION_AWAITING_REVIEW)
    return _commit_review(row, "dont_read", surface="review_reject", write_to_golden=write_to_golden)


def relabel(processed_id: int, new_priority: str) -> dict[str, Any]:
    """Override an awaiting or gate-rejected row with a deliberate label."""
    if new_priority not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(
            f"new_priority must be one of {sorted(_PRIORITY_TO_RELEVANCE)}; got {new_priority!r}"
        )
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
    _require_actionable(row)
    return _commit_review(row, new_priority, surface="review_relabel")


def _commit_review(
    row: dict[str, Any], priority: str, *, surface: str, write_to_golden: bool = True,
) -> dict[str, Any]:
    """Commit the decision, label and training metadata in one SQLite transaction."""
    payload = None
    if priority != "dont_read":
        summary = _build_summary_for_queue(row, priority)
        payload = parse_payload(row)
        payload["summary"] = summary.model_dump()
    comment = f"{surface}: {priority}; from {row['decision']}"
    sample = (
        {key: str(value) for key, value in asdict(
            prepare_training_sample(row, label=priority, note=comment),
        ).items()} if write_to_golden else None
    )
    item_key = row_feed_keys(row)[0]
    new_state = (
        feeds_storage.DECISION_USER_REJECTED if priority == "dont_read"
        else feeds_storage.DECISION_USER_APPROVED
    )
    reason = (
        f"user_relabel:{priority}:from_{row['decision']}" if surface == "review_relabel"
        else f"{new_state}_in_review_ui"
    )
    original = str(row.get("reading_priority") or "").strip() or "unknown"
    with _conn() as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _fetch_row(conn, int(row["id"]))
        _require_state(current, row["decision"])
        _, previous = repositories.upsert_label_verdict(
            conn, item_key=item_key, original_derived_priority=original,
            user_priority=priority, comment=comment,
            training_sample=sample,
        )
        if payload is not None:
            conn.execute(
                "UPDATE processed_feed_items SET shap_contribs_json = ?, reading_priority = ? WHERE id = ?",
                (_json.dumps(payload), priority, int(row["id"])),
            )
        feeds_storage.update_to_decision(
            conn, feed_library_id=int(row["feed_library_id"]), feed_item_id=int(row["feed_item_id"]),
            decision=new_state, decision_reason=reason,
        )
    label_verdicts.log_committed_transition(
        item_key=item_key, previous=previous or {}, new_user_priority=priority,
        model_priority=original, surface="review_label", source=VERDICT_SOURCE_USER, comment=comment,
    )
    _log_review(row, surface, priority)
    return {"processed_id": int(row["id"]), "state": new_state}


def confirm_remaining_gate_rejected(processed_ids: list[int]) -> dict[str, int]:
    """Confirm the requested gate rejects; skip rows already acted on.

    Each row uses the same durable decision/label command as individual review.
    A partial failure propagates; retry skips completed rows. CSV duplicates
    still receive the explicit verdict used by the training overlay.
    """
    with _conn() as conn:
        rows = [_fetch_row(conn, pk) for pk in dict.fromkeys(processed_ids)]
    confirmed = 0
    for row in rows:
        if row["decision"] != feeds_storage.DECISION_GATE_REJECTED:
            continue
        _commit_review(row, "dont_read", surface="review_confirm_gate_rejected")
        confirmed += 1
    return {"confirmed": confirmed, "skipped": len(rows) - confirmed}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ACTIONABLE_STATES = frozenset({
    feeds_storage.DECISION_AWAITING_REVIEW,
    feeds_storage.DECISION_GATE_REJECTED,
})


def _require_state(row: dict[str, Any], expected: str) -> None:
    if row.get("decision") != expected:
        raise ValueError(
            f"row id={row.get('id')} is in state {row.get('decision')!r}, "
            f"expected {expected!r}"
        )


def _require_actionable(row: dict[str, Any]) -> None:
    """Allow review-UI mutations on awaiting_review OR gate_rejected rows."""
    if row.get("decision") not in _ACTIONABLE_STATES:
        raise ValueError(
            f"row id={row.get('id')} is in state {row.get('decision')!r}, "
            f"expected one of {sorted(_ACTIONABLE_STATES)}"
        )


from zotero_summarizer.services.library.review_summary import (  # noqa: E402,F401  (re-export)
    _build_summary_for_queue,
    _drop_trashed_rearrivals,
    _fetch_feed_metadata,
    _unpack_summary,
    _write_golden_sample,
    append_to_golden,
    append_verdict_to_golden,
    pick_stored_summary,
)
from zotero_summarizer.services.library.review_materialize import (  # noqa: E402,F401  (re-export)
    apply_all_approved,
    materialize_row,
)
