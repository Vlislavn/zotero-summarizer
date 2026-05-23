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
from contextlib import contextmanager
from typing import Any, Iterator

from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden.goldenset import _PRIORITY_TO_RELEVANCE
from zotero_summarizer.storage import feeds as feeds_storage

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper (mirrors feeds.py)
# ---------------------------------------------------------------------------


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = get_settings().triage_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        feeds_storage.init_feeds_schema(conn)
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_awaiting(since_hours: int = 720, limit: int = 1000) -> list[dict[str, Any]]:
    """Return every ``awaiting_review`` row enriched with parsed SHAP/aux/summary."""
    return list_by_state(feeds_storage.DECISION_AWAITING_REVIEW, since_hours, limit)


def list_by_state(state: str, since_hours: int = 720, limit: int = 1000) -> list[dict[str, Any]]:
    """Return every row with ``decision == state`` enriched with parsed payload.

    The review UI uses this for both ``awaiting_review`` (the LLM-or-gate-only
    triage queue) and ``gate_rejected`` (items the classifier dropped before
    LLM — exposed so the user can spot-check false negatives and relabel).
    """
    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[state],
            since_hours=since_hours,
            limit=limit,
        )
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


def materialize_row(
    row: dict[str, Any],
    *,
    writer: Any,
    used_keys: set[str],
    reason: str = "review_apply",
) -> str:
    """Materialize ONE feed row into Zotero and return the new item key.

    Creates the item in the "Inbox" collection (+ any matched collections),
    flips the row to ``DECISION_SELECTED``, stamps ``materialized_zotero_key``
    and schedules the 7-day outcome window. Raises on failure — callers
    running a batch wrap each call (one locked row must not strand the rest).

    Shared by :func:`apply_all_approved` (review UI) and
    ``services.daily_actions.add_to_library`` (Today's add-to-library).
    """
    from zotero_summarizer.services.zotero import pending as pending_service
    from zotero_summarizer.services.triage.feeds import (
        _feed_payload_from_row, _generate_zotero_key, _matched_collections_from_row,
        _summary_from_row, _tags_from_row,
    )

    row_id = int(row["id"])
    new_key = _generate_zotero_key(used_keys)
    stored = _pick_summary_for_apply(row)
    summary = stored if stored is not None else _summary_from_row(row)
    feed_payload = _feed_payload_from_row(row)
    tags = _tags_from_row(row, is_black_swan=False, black_swan_tag="")
    note_html = pending_service.build_triage_note_html(
        title=str(row.get("title") or ""),
        summary=summary,
        is_black_swan=False,
        surprise_score=None,
        run_id=f"{reason}:{row_id}",
    )
    writer.apply_feed_materialization(
        new_item_key=new_key,
        feed_payload=feed_payload,
        inbox_collection_name="Inbox",   # matches daemon default
        matched_collections=_matched_collections_from_row(row),
        tags=tags,
        note_title=f"Triage: {str(row.get('title') or '')[:80]}",
        note_html=note_html,
        provenance_tag=pending_service.SYSTEM_TAG_FEEDS_V3,
    )
    with _conn() as conn:
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_SELECTED,
            decision_reason=f"materialized_via_{reason}",
            planned_zotero_key=new_key,
        )
        feeds_storage.record_materialization(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            materialized_zotero_key=new_key,
            outcome_window_days=7,
        )
        conn.commit()
    return new_key


def apply_all_approved(since_hours: int = 720) -> dict[str, Any]:
    """Materialize every ``user_approved`` row into Zotero.

    Bypasses the pending_changes pipeline (which is designed for existing
    library items) and calls :meth:`ZoteroWriter.apply_feed_materialization`
    — the same daemon-direct path used by ``run_daily_selection``. Per-row
    failures are caught + logged + reported in the response (batch contract:
    one bad row must not block the rest of the user's queue).

    On success: row → ``DECISION_SELECTED`` + ``materialized_zotero_key``
    stamped + 7-day outcome window scheduled.

    Returns ``{"applied", "failed_count", "failed": [{"id", "title", "error"}, ...]}``.
    """
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter

    settings_ = get_settings()
    writer = ZoteroWriter(settings_.zotero_data_dir)

    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[feeds_storage.DECISION_USER_APPROVED],
            since_hours=since_hours,
            limit=5000,
        )

    applied = 0
    failed: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for row in rows:
        row_id = int(row["id"])
        try:
            materialize_row(row, writer=writer, used_keys=used_keys, reason="review_apply")
            applied += 1
        except Exception as exc:
            # Batch-apply contract: log and continue so one Zotero-locked row
            # doesn't strand the rest of the user's approvals. Per-row error
            # is surfaced in the response.
            LOGGER.exception("apply_all_approved failed for row id=%s", row_id)
            failed.append({
                "id": row_id,
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })

    return {
        "applied": applied,
        "failed_count": len(failed),
        "failed": failed[:20],
    }


def _pick_summary_for_apply(row: dict[str, Any]):
    """Return the stored SummarizeResponse (or None) from shap_contribs_json.

    Used by apply_all_approved to prefer the LLM/relabel-synthesised summary
    over the sparse fallback rebuilt from the row's scalar fields.
    """
    blob = (row.get("shap_contribs_json") or "").strip()
    if not blob:
        return None
    payload = _json.loads(blob)
    summary_dict = payload.get("summary")
    if summary_dict is None:
        return None
    return SummarizeResponse.model_validate(summary_dict)


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
    _fetch_feed_metadata,
    _unpack_summary,
    _write_golden_sample,
    append_to_golden,
    append_verdict_to_golden,
)
