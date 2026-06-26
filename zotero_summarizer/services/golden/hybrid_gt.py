"""Hybrid ground-truth loader.

The golden CSV stores *derived* labels: ``gold_priority_final`` is produced
by ``services/goldenset.py:_infer_label`` from emoji tags, annotation
count, note count, and 180-day age decay. The user can override that
derivation via ``POST /api/golden/verdict`` (Annotate UI), which writes
to the ``label_verdicts`` table in ``triage_history.db``.

Until Phase 1.18 Step 2 (this module), nobody read ``label_verdicts``:
user verdicts existed in the DB but no ML pipeline consulted them. This
module is the single point where derived + user-overridden labels merge
into the *effective* ground truth that classifiers, calibration, and
metrics all use.

June 2026 — outcome correction. "Add to library" (Today) writes a PROVISIONAL
positive verdict (``label_verdicts.source = 'machine_add'``) before the paper
has been read. The feeds daemon later resolves a 7-day materialization outcome
(``processed_feed_items.final_outcome``: engaged / moved_collection /
kept_inbox / trashed / deleted_all). Previously that observed behaviour never
reached training, so "added then never touched" papers stayed top-band
positives forever. The merge below now corrects provisional verdicts with the
observed outcome. The correction is DEMOTE-ONLY (``min`` of provisional and
outcome-mapped relevance): promotions already flow through the Zotero
engagement export as a full-weight row on the materialized item, so promoting
here would double-count.

Source-of-truth precedence (highest first):
    1. explicit user verdict (Annotate UI / Review relabel / trash /
       Zotero ``label:*`` reconcile)              -> source="user"
    2. provisional machine add + resolved outcome -> source="outcome"
    3. provisional machine add, window pending    -> source="machine_add"
    4. gold_priority_final from CSV               -> source="derived"
    5. row missing from both stores               -> not in the result dict

Single responsibility: read both stores, merge, return. No I/O beyond the
reads. Callers that need per-row metadata still load the CSV themselves and
call ``apply_hybrid`` on each row.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from zotero_summarizer.domain import (
    PRIORITY_TO_RELEVANCE as _PRIORITY_TO_RELEVANCE,
    ReadingPriority,
    VERDICT_SOURCE_MACHINE_ADD,
    score_to_priority,
)
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories


SOURCE_DERIVED = "derived"
SOURCE_USER = "user"
# Provisional "Add to library" verdict whose outcome window has not resolved.
SOURCE_MACHINE = VERDICT_SOURCE_MACHINE_ADD
# Provisional verdict corrected by the observed 7-day materialization outcome.
SOURCE_OUTCOME = "outcome"

# An unresolved "Add to library" (source=machine_add) is a PROVISIONAL interest
# signal, not a verified reading decision: the user moved it Today→library but
# has not checked the label. Its effective TRAINING relevance is therefore capped
# at a weak ``could_read`` (3.0), NOT the ``should_read`` (4.0) the add action
# stamps for display intent — otherwise an unread "Add" becomes an unverified
# top-band positive (~13% of training-eligible rows; user decision 2026-06-19:
# "capture the transfer, but the label isn't actually checked"). An explicit user
# verdict, an emoji/engagement signal, or a 7-day behavioural outcome overrides it.
_UNCHECKED_ADD_PRIORITY = ReadingPriority.COULD_READ.value
_UNCHECKED_ADD_RELEVANCE = float(_PRIORITY_TO_RELEVANCE[_UNCHECKED_ADD_PRIORITY])


def load_user_verdicts(db_path: Path) -> dict[str, dict[str, Any]]:
    """Return ``{item_key: verdict_row}`` for EVERY recorded user verdict.

    Uses the uncapped reader: the paged ``list_label_verdicts`` default cap
    silently dropped the oldest verdicts from training once the table
    outgrew it (found June 2026 with 946 rows vs the 500 cap).
    """
    rows = repositories.list_all_label_verdicts(db_path)
    return {row["item_key"]: row for row in rows}


def load_resolved_outcomes(db_path: Path) -> dict[str, str]:
    """``{"feed:<feed_item_id>": final_outcome}`` for behavioural outcomes.

    Only outcomes in ``BEHAVIORAL_OUTCOMES`` are returned — ``pending``
    (window not elapsed) and ``unknown`` (key-resolution failure) carry no
    observed user behaviour and must never correct a label.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        by_id = feeds_storage.fetch_resolved_outcomes(
            conn, outcomes=tuple(sorted(feeds_storage.BEHAVIORAL_OUTCOMES))
        )
    finally:
        conn.close()
    return {f"feed:{fid}": outcome for fid, outcome in by_id.items()}


def outcome_correction(
    provisional_priority: str, outcome: str
) -> tuple[str, float] | None:
    """Demote-only correction of a provisional add label by its outcome.

    Mechanical — no per-outcome branches: the outcome's ``OUTCOME_WEIGHT``
    maps onto the relevance scale via the shared linear map, and the
    corrected relevance is ``min(provisional, mapped)``. A new outcome value
    added to the taxonomy gets sane behaviour for free; a non-behavioural
    outcome returns ``None`` (no correction).
    """
    if outcome not in feeds_storage.BEHAVIORAL_OUTCOMES:
        return None
    mapped = feeds_storage.relevance_from_signal_weight(
        feeds_storage.OUTCOME_WEIGHT[outcome]
    )
    corrected = min(float(_PRIORITY_TO_RELEVANCE[provisional_priority]), mapped)
    return score_to_priority(corrected), corrected


def _resolve_verdict(
    verdict: dict[str, Any], outcome: str | None
) -> tuple[str, float, str]:
    """Apply the precedence ladder to one verdict row.

    Returns ``(priority, relevance, source)`` where source is one of
    ``SOURCE_USER`` / ``SOURCE_OUTCOME`` / ``SOURCE_MACHINE``.
    """
    user_priority = verdict["user_priority"]
    if verdict.get("source") == VERDICT_SOURCE_MACHINE_ADD:
        # Provisional/unchecked: cap the effective label at weak could_read (the
        # demote-only outcome floor uses the same cap, so a behavioural outcome
        # can only hold-flat-or-lower it, never promote an unread add).
        if outcome is not None:
            correction = outcome_correction(_UNCHECKED_ADD_PRIORITY, outcome)
            if correction is not None:
                priority, relevance = correction
                return priority, relevance, SOURCE_OUTCOME
        return _UNCHECKED_ADD_PRIORITY, _UNCHECKED_ADD_RELEVANCE, SOURCE_MACHINE
    return user_priority, float(_PRIORITY_TO_RELEVANCE[user_priority]), SOURCE_USER


def load_hybrid_labels(
    csv_path: Path,
    db_path: Path,
) -> dict[str, dict[str, Any]]:
    """Merge derived (CSV) + user (DB) labels into one dict.

    Every row that appears in either source gets an entry. Keys with both
    sources resolve via the precedence ladder (module docstring). Missing CSV
    entries are valid: a user verdict can exist before the next
    ``goldenset export`` runs.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}")

    user = load_user_verdicts(db_path)
    outcomes = load_resolved_outcomes(db_path)
    out: dict[str, dict[str, Any]] = {}

    def _entry(key: str, derived_priority: str | None) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "item_key": key,
            "derived_priority": derived_priority,
            "user_priority": None,
            "effective_priority": derived_priority,
            "source": SOURCE_DERIVED,
            "comment": "",
        }
        v = user.get(key)
        if v is not None:
            priority, _relevance, source = _resolve_verdict(v, outcomes.get(key))
            entry["user_priority"] = v["user_priority"]
            entry["effective_priority"] = priority
            entry["source"] = source
            entry["comment"] = v.get("comment", "") or ""
        return entry

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("item_key") or "").strip()
            if not key:
                continue
            derived = (row.get("gold_priority_final") or "").strip()
            out[key] = _entry(key, derived or None)

    # User verdicts on rows the CSV has not seen yet (e.g. fresh feed:* row
    # marked before the next goldenset export). Keep them — the gate
    # retrainer should still see the user's signal.
    for key in user:
        if key not in out:
            out[key] = _entry(key, None)
    return out


def apply_hybrid(
    rows: Iterable[dict[str, Any]],
    db_path: Path,
) -> list[dict[str, Any]]:
    """Stream-overlay the effective ground truth onto pre-loaded CSV rows.

    Cheaper than ``load_hybrid_labels`` when you already hold the CSV rows
    in memory (the classifier does). Each input row is shallow-copied and
    has ``gold_priority_final`` + ``gold_inferred_relevance`` replaced with
    the ladder-resolved label. A ``_hybrid_source`` field is added so
    downstream code can audit which rows came from user input vs. outcome
    correction vs. derivation.

    Outcome-corrected rows additionally get an ``outcome_<name>`` segment
    appended to ``gold_signal_tier`` — ``services.model.label_weights`` keys
    the "resolved observation" confidence weight on that segment.
    """
    user = load_user_verdicts(db_path)
    outcomes = load_resolved_outcomes(db_path)
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (row.get("item_key") or "").strip()
        new = dict(row)
        v = user.get(key) if key else None
        if v is not None:
            priority, relevance, source = _resolve_verdict(v, outcomes.get(key))
            new["gold_priority_final"] = priority
            # Sprint-1: also overwrite the continuous relevance so the
            # regression target reflects the effective verdict, not the
            # stale derivation from emojis/annotations.
            new["gold_inferred_relevance"] = str(relevance)
            new["_hybrid_source"] = source
            new["_hybrid_comment"] = v.get("comment", "") or ""
            if source == SOURCE_OUTCOME:
                tier = (new.get("gold_signal_tier") or "").strip()
                suffix = f"outcome_{outcomes[key]}"
                new["gold_signal_tier"] = f"{tier}|{suffix}" if tier else suffix
        else:
            new["_hybrid_source"] = SOURCE_DERIVED
        out.append(new)
    return out


def hybrid_summary(
    csv_path: Path,
    db_path: Path,
) -> dict[str, Any]:
    """Aggregate counts for the UI: how many rows have a user verdict, and
    how many of those changed the priority vs. matched the derivation.

    Machine-written provisional adds are reported separately from deliberate
    user verdicts (they used to inflate ``user_verdicts``), and the subset
    corrected by an observed outcome is surfaced as ``outcome_corrected``.
    """
    merged = load_hybrid_labels(csv_path, db_path)
    n_total = len(merged)
    n_user = sum(1 for e in merged.values() if e["source"] == SOURCE_USER)
    n_user_changed = sum(
        1 for e in merged.values()
        if e["source"] == SOURCE_USER
        and e["derived_priority"] is not None
        and e["user_priority"] != e["derived_priority"]
    )
    n_user_confirmed = n_user - n_user_changed
    n_machine = sum(1 for e in merged.values() if e["source"] == SOURCE_MACHINE)
    n_outcome = sum(1 for e in merged.values() if e["source"] == SOURCE_OUTCOME)
    return {
        "total_rows": n_total,
        "user_verdicts": n_user,
        "user_confirmed_derivation": n_user_confirmed,
        "user_overrode_derivation": n_user_changed,
        "machine_provisional": n_machine,
        "outcome_corrected": n_outcome,
    }
