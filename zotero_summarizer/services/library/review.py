"""Phase 1.14 — review-mode service layer.

Operations against ``processed_feed_items`` rows whose ``decision`` is
``awaiting_review``. The web UI calls these via :mod:`api.routes.review`.

Single responsibility: state transitions for review-mode items + golden CSV
append. No HTTP concerns (those live in the route module).
"""
from __future__ import annotations

import json as _json
import logging
import sqlite3
from typing import Any

from zotero_summarizer.services import interaction_log
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden.goldenset import _PRIORITY_TO_RELEVANCE
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage.feed_identity import row_feed_keys

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper (mirrors feeds.py)
# ---------------------------------------------------------------------------


def _conn():
    return feeds_storage.open_triage_conn(get_settings().triage_db_path)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_by_state(state: str, since_hours: int = 720, limit: int = 1000) -> list[dict[str, Any]]:
    """Return every row with ``decision == state`` enriched with parsed payload.

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
        )
        rows = _drop_trashed_rearrivals(conn, rows)
    return [_decorate_row(r) for r in rows]


def _decorate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON payload column into structured fields for the UI."""
    out = dict(row)
    blob = (row.get("shap_contribs_json") or "").strip()
    payload: dict[str, Any] = {}
    if blob:
        payload = _json.loads(blob)
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


def _record_label_verdict(row: dict[str, Any], priority: str, comment: str) -> None:
    """Persist a review decision into ``label_verdicts`` when the table exists."""
    try:
        label_verdicts.set_label_verdict(
            get_settings().triage_db_path,
            item_key=row_feed_keys(row)[0],
            original_derived_priority=str(row.get("reading_priority") or "").strip() or "unknown",
            user_priority=priority,
            surface="review_label",
            comment=comment,
        )
    except sqlite3.Error as exc:
        LOGGER.warning("review label verdict could not be saved: %s", exc)


# ---------------------------------------------------------------------------
# User actions
# ---------------------------------------------------------------------------


def approve(processed_id: int) -> dict[str, Any]:
    """Flip awaiting_review → user_approved.

    Does NOT queue pending_changes — feed items don't exist in the user's
    Zotero library yet, so the library-centric pending-changes pipeline
    (which expects an existing item_key) would fail with "Item not found".
    The actual Zotero write happens later in :func:`apply_all_approved`,
    which calls ``writer.apply_feed_materialization`` (the daemon's
    direct-create path).
    """
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_state(row, feeds_storage.DECISION_AWAITING_REVIEW)
        # _unpack_summary is a sanity check — fails fast if the row predates
        # Phase 1.14 and has no stored LLM summary.
        _unpack_summary(row)
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_USER_APPROVED,
            decision_reason="user_approved_in_review_ui",
        )
        conn.commit()
    priority = _priority_for_positive_review(row)
    _record_label_verdict(row, priority, "approved in review UI")
    return {"processed_id": processed_id, "state": feeds_storage.DECISION_USER_APPROVED}


def reject(processed_id: int, *, write_to_golden: bool = True) -> dict[str, Any]:
    """Flip awaiting_review → user_rejected; optionally append dont_read to golden CSV."""
    return _label_and_terminate(
        processed_id,
        new_state=feeds_storage.DECISION_USER_REJECTED,
        label="dont_read",
        write_to_golden=write_to_golden,
        reason="user_rejected_in_review_ui",
    )


def relabel(processed_id: int, new_priority: str) -> dict[str, Any]:
    """Override priority + append to golden CSV.

    ``new_priority`` must be one of must_read / should_read / could_read /
    dont_read.

    Accepts rows in either ``awaiting_review`` (the LLM/gate-only triage
    queue) or ``gate_rejected`` (items the gate dropped pre-LLM). The latter
    lets the user correct false negatives — they have no stored LLM summary,
    so a minimal SummarizeResponse is synthesised on the fly when needed.

    Outcomes:
      * ``new_priority == "dont_read"``: row moves to ``user_rejected``;
        golden CSV gets a dont_read row (positive confirmation if the
        original was gate_rejected, terminal rejection if awaiting_review).
      * other priorities: row moves to ``user_approved``, pending_changes
        queued for Zotero materialisation, golden CSV gets the new label.
    """
    if new_priority not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(
            f"new_priority must be one of {sorted(_PRIORITY_TO_RELEVANCE)}; got {new_priority!r}"
        )
    if new_priority == "dont_read":
        return _confirm_or_reject_to_dont_read(processed_id)
    # Approve-track relabel: flip state to user_approved, persist the chosen
    # priority into the row's payload so `apply_all_approved` can build the
    # right note, append golden CSV. NO pending_changes queueing — feed items
    # don't exist in Zotero yet; materialization happens in apply_all_approved.
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_actionable(row)
        _store_relabel_priority(conn, row, new_priority)
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_USER_APPROVED,
            decision_reason=f"user_relabel:{new_priority}:from_{row.get('decision')}",
        )
        conn.commit()
    appended = append_to_golden(
        row,
        label=new_priority,
        note=f"relabel via review UI ({new_priority}; from {row.get('decision')})",
    )
    _record_label_verdict(
        row,
        new_priority,
        f"relabel via review UI ({new_priority}; from {row.get('decision')})",
    )
    _log_review(row, "review_relabel", new_priority)
    return {
        "processed_id": processed_id,
        "state": feeds_storage.DECISION_USER_APPROVED,
        "golden_csv_row_added": appended,
    }


def _store_relabel_priority(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    new_priority: str,
) -> None:
    """Persist the relabel target back into shap_contribs_json so that
    :func:`apply_all_approved` can synthesise the correct note later.

    For awaiting_review items: overrides ``summary.reading_priority``.
    For gate_rejected items: synthesises a minimal summary if absent.
    """
    summary = _build_summary_for_queue(row, new_priority)
    blob = (row.get("shap_contribs_json") or "").strip()
    payload: dict[str, Any] = _json.loads(blob) if blob else {}
    payload["summary"] = summary.model_dump()
    conn.execute(
        "UPDATE processed_feed_items SET shap_contribs_json = ?, "
        "reading_priority = ?, updated_at = datetime('now') WHERE id = ?",
        (_json.dumps(payload), new_priority, int(row["id"])),
    )


def _confirm_or_reject_to_dont_read(processed_id: int) -> dict[str, Any]:
    """``relabel(dont_read)`` for both awaiting_review and gate_rejected.

    Flips the row to ``user_rejected`` and appends a dont_read row to the
    golden CSV. For gate_rejected items this means "user confirmed the gate"
    — strong training signal that the model was right.
    """
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_actionable(row)
        prior_state = row.get("decision")
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_USER_REJECTED,
            decision_reason=f"user_relabel:dont_read:from_{prior_state}",
        )
        conn.commit()
    appended = append_to_golden(
        row,
        label="dont_read",
        note=f"relabel via review UI (dont_read; from {prior_state})",
    )
    _record_label_verdict(row, "dont_read", f"relabel via review UI (dont_read; from {prior_state})")
    _log_review(row, "review_relabel", "dont_read")
    return {"processed_id": processed_id, "golden_csv_row_added": appended}


def confirm_remaining_gate_rejected(since_hours: int = 720) -> dict[str, Any]:
    """Bulk-confirm: append a dont_read row to golden CSV for every
    ``gate_rejected`` item the user hasn't already relabelled.

    Semantics: "no click = confirmation" — the user has implicitly agreed
    that the gate was correct for these items. Idempotent: rows whose
    item_key is already in the golden CSV are skipped (append_to_golden
    detects duplicates).

    Decision in DB stays ``gate_rejected`` — the user didn't act, they just
    confirmed the model's verdict. Subsequent retrain picks up the new
    negative-class rows from the golden CSV.

    Returns ``{"appended", "skipped_duplicate", "skipped_no_feed_id"}``.
    """
    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[feeds_storage.DECISION_GATE_REJECTED],
            since_hours=since_hours,
            limit=10000,
        )
    appended = 0
    skipped_duplicate = 0
    skipped_no_feed_id = 0
    for row in rows:
        if int(row.get("feed_item_id") or 0) <= 0:
            skipped_no_feed_id += 1
            continue
        was_new = append_to_golden(
            row,
            label="dont_read",
            note="implicit_confirm_gate_rejected (no user action in review UI)",
        )
        if was_new:
            appended += 1
        else:
            skipped_duplicate += 1
    return {
        "appended": appended,
        "skipped_duplicate": skipped_duplicate,
        "skipped_no_feed_id": skipped_no_feed_id,
        "total_considered": len(rows),
    }


def _label_and_terminate(
    processed_id: int,
    *,
    new_state: str,
    label: str,
    write_to_golden: bool,
    reason: str,
) -> dict[str, Any]:
    """Reject/terminal-relabel path: flip state, optionally update golden CSV."""
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_state(row, feeds_storage.DECISION_AWAITING_REVIEW)
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=new_state,
            decision_reason=reason,
        )
        conn.commit()
    appended = False
    if write_to_golden:
        appended = append_to_golden(row, label=label, note=f"{reason} via review UI")
    _record_label_verdict(row, label, f"{reason} via review UI")
    _log_review(row, "review_reject", label)
    return {"processed_id": processed_id, "golden_csv_row_added": appended}


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


# Integer relevance scores for the SummarizeResponse synthesised at relabel
# time. Matches `SummarizeResponse.relevance_score: int = Field(..., ge=1, le=5)`.


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
