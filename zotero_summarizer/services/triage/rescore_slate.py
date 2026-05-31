"""Re-score the current Today slate IN PLACE with the loaded classifier gate.

After a gate upgrade (e.g. the SOTA prestige-signal change), already-triaged
slate rows keep the scores they were given at triage time — the triage state
machine treats decisions as terminal and never re-scores them. This module
re-runs the *current* in-memory gate over the slate's candidate pool (the
``awaiting_review`` + ``triaged_pending`` rows the Today tab shows) and rewrites
ONLY their gate-derived fields (``composite_score`` / ``reading_priority`` /
``shap_contribs_json``) via :func:`storage.feeds.update_scores`.

Guarantees:
  * **No decision / read-status change** — handled items stay handled and are
    never re-surfaced (we even skip rows the user already acted on).
  * **Live gate** — uses ``get_state().classifier_gate``, so restart the server
    first if you just trained a new artifact with an unchanged golden-CSV sha
    (the daemon's auto-swap only fires on a sha drift).
  * Faithful to the gate-only drain: ``composite_score = calibrated_score·5``,
    ``reading_priority = predicted_priority`` — the same mapping ``_gate`` writes.

Limitation: the feed item's original venue string isn't persisted, so the minor
``has_venue`` feature is reconstructed from the stored OpenAlex venue when
available; the prestige feature (the thing that changed) reconstructs exactly
from the cached OpenAlex ``citation_percentile``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.triage.daily_select._candidate import row_prestige
from zotero_summarizer.services.triage.daily_select._querying import (
    fetch_handled_keys,
    fetch_recent_rows_by_decisions,
    fetch_rows_by_decisions,
)
from zotero_summarizer.storage import feeds as feeds_storage

LOGGER = logging.getLogger(__name__)

_PRIMARY_DECISIONS = [
    feeds_storage.DECISION_AWAITING_REVIEW,
    feeds_storage.DECISION_TRIAGED_PENDING,
]


def _venue_from_payload(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return ""
    return str(summary.get("prestige_venue") or summary.get("venue") or "")


def _item_from_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the gate-input item dict from a stored row (see module
    limitation re: venue)."""
    guid = (row.get("guid") or "").strip()
    return {
        "item_key": guid or f"row-{row['id']}",
        "item_id": row.get("feed_item_id"),
        "title": row.get("title") or "",
        "abstract": row.get("abstract") or "",
        "doi": (row.get("doi") or "").strip(),
        # gate reads (publication_date or year)[:4]; pub_year is the stored year.
        "year": str(row.get("pub_year") or ""),
        "venue": _venue_from_payload(payload),
    }


def _repack_payload(old_payload: dict[str, Any], pred: Any) -> str:
    """Rebuild ``shap_contribs_json``: refresh the gate's SHAP + aux_context
    (now carrying ``citation_percentile``) while PRESERVING the LLM summary
    (authors/venue/rationale) and any audit marker the row already had."""
    summary = old_payload.get("summary") if isinstance(old_payload, dict) else None
    payload: dict[str, Any] = {
        "shap": pred.shap_contribs,
        "aux_context": pred.aux_context,
        "summary": summary,
    }
    if isinstance(old_payload, dict) and old_payload.get("audit_pick"):
        payload["audit_pick"] = True
    return json.dumps(payload)


def _unhandled(rows: list[dict[str, Any]], conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Drop rows the user already acted on (rated/labeled) — same rule the slate
    uses — so a re-score never resurfaces a handled paper."""
    handled_guids, handled_label_keys = fetch_handled_keys(conn)
    out: list[dict[str, Any]] = []
    for row in rows:
        guid = str(row.get("guid") or "").strip()
        if guid and guid in handled_guids:
            continue
        fid = row.get("feed_item_id")
        if fid is not None and f"feed:{int(fid)}" in handled_label_keys:
            continue
        out.append(row)
    return out


def _priority_hist(rows: list[dict[str, Any]]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for r in rows:
        hist[r.get("reading_priority") or "?"] = hist.get(r.get("reading_priority") or "?", 0) + 1
    return hist


def rescore_slate(*, lookback_hours: int = 168, backlog_cap: int = 25) -> dict[str, Any]:
    """Re-score the current Today slate pool in place. Returns counts +
    before/after distributions. Message-only no-op when the gate is off."""
    gate = getattr(get_state(), "classifier_gate", None)
    if gate is None:
        return {"rescored": 0, "message": "Classifier gate not loaded — restart the server first."}

    settings_ = get_settings()
    config = get_state().app_state.config
    now = datetime.now(timezone.utc)

    conn = sqlite3.connect(str(settings_.triage_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in fetch_rows_by_decisions(
            conn, decisions=_PRIMARY_DECISIONS, lookback_hours=lookback_hours, now=now,
        )]
        if not rows:  # never-empty fallback, mirrors the slate
            rows = [dict(r) for r in fetch_recent_rows_by_decisions(
                conn, decisions=_PRIMARY_DECISIONS, limit=backlog_cap,
            )]
        rows = _unhandled(rows, conn)
        scorable = [r for r in rows if (r.get("abstract") or "").strip()]
        if not scorable:
            return {"rescored": 0, "message": "No scorable slate rows in window.", "candidates": len(rows)}

        payloads = {r["id"]: (json.loads(r["shap_contribs_json"]) if (r.get("shap_contribs_json") or "").strip() else {})
                    for r in scorable}
        items = [_item_from_row(r, payloads[r["id"]]) for r in scorable]
        preds = gate.predict(
            items, corpus_db_path=settings_.corpus_db_path,
            goals_config=config, return_shap=True,
        )
        by_key = {p.item_key: p for p in preds}

        before_priority = _priority_hist(scorable)
        before_prestige = [row_prestige(r, payloads[r["id"]]) for r in scorable]

        rescored = 0
        skipped = 0
        after_rows: list[dict[str, Any]] = []
        for row, item in zip(scorable, items):
            pred = by_key.get(item["item_key"])
            if pred is None:  # featurisation skipped (no title/abstract) — leave as-is
                skipped += 1
                continue
            composite = float(pred.calibrated_score) * 5.0
            new_json = _repack_payload(payloads[row["id"]], pred)
            feeds_storage.update_scores(
                conn, row_id=int(row["id"]),
                composite_score=composite,
                reading_priority=pred.predicted_priority,
                shap_contribs_json=new_json,
            )
            rescored += 1
            after_rows.append({
                "reading_priority": pred.predicted_priority,
                "_payload": json.loads(new_json),
            })
        conn.commit()
    finally:
        conn.close()

    after_prestige = [row_prestige({}, ar["_payload"]) for ar in after_rows]
    gate_sha = getattr(gate, "golden_csv_sha256", "")
    LOGGER.info("rescore_slate: rescored=%d skipped=%d gate=%s", rescored, skipped, gate_sha[:12])
    return {
        "rescored": rescored,
        "skipped": skipped,
        "candidates": len(rows),
        "gate_sha": gate_sha,
        "before": {
            "by_priority": before_priority,
            "prestige_known": sum(1 for p in before_prestige if p > 0),
        },
        "after": {
            "by_priority": _priority_hist(after_rows),
            "prestige_known": sum(1 for p in after_prestige if p > 0),
        },
    }
