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

Contract:

    load_hybrid_labels(csv_path, db_path) ->
        {item_key: {"user_priority", "source": "derived" | "user", ...}}

Source-of-truth precedence:
    1. user_priority from label_verdicts  -> source="user"
    2. gold_priority_final from CSV       -> source="derived"
    3. row missing from both              -> not in the result dict

Single responsibility: read both stores, merge, return. No I/O beyond the
two reads. Callers that need per-row metadata still load the CSV
themselves and call ``apply_hybrid`` on each row.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

from zotero_summarizer.storage import repositories


SOURCE_DERIVED = "derived"
SOURCE_USER = "user"


# Sprint-1 (May 2026): the regressor trains on `gold_inferred_relevance`
# (continuous [1,5]) — NOT on `gold_priority_final` (4-class label). When
# a user verdict overrides the priority class, we must also write a
# matching continuous score, else the model's target column stays at the
# stale derivation. Mapping matches the canonical values produced by
# `goldenset._infer_label` for hard veto / strong positive.
from zotero_summarizer.domain import PRIORITY_TO_RELEVANCE as _PRIORITY_TO_RELEVANCE  # noqa: E402


def load_user_verdicts(db_path: Path) -> dict[str, dict[str, Any]]:
    """Return ``{item_key: verdict_row}`` for every recorded user verdict."""
    rows = repositories.list_label_verdicts(db_path)
    return {row["item_key"]: row for row in rows}


def load_hybrid_labels(
    csv_path: Path,
    db_path: Path,
) -> dict[str, dict[str, Any]]:
    """Merge derived (CSV) + user (DB) labels into one dict.

    Every row that appears in either source gets an entry. Keys with both
    sources resolve to user (precedence). Missing CSV entries are valid:
    a user verdict can exist before the next ``goldenset export`` runs.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}")

    user = load_user_verdicts(db_path)
    out: dict[str, dict[str, Any]] = {}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("item_key") or "").strip()
            if not key:
                continue
            derived_priority = (row.get("gold_priority_final") or "").strip()
            entry: dict[str, Any] = {
                "item_key": key,
                "derived_priority": derived_priority or None,
                "user_priority": None,
                "effective_priority": derived_priority or None,
                "source": SOURCE_DERIVED,
                "comment": "",
            }
            v = user.get(key)
            if v is not None:
                entry["user_priority"] = v["user_priority"]
                entry["effective_priority"] = v["user_priority"]
                entry["source"] = SOURCE_USER
                entry["comment"] = v.get("comment", "") or ""
            out[key] = entry

    # User verdicts on rows the CSV has not seen yet (e.g. fresh feed:* row
    # marked before the next goldenset export). Keep them — the gate
    # retrainer should still see the user's signal.
    for key, v in user.items():
        if key in out:
            continue
        out[key] = {
            "item_key": key,
            "derived_priority": None,
            "user_priority": v["user_priority"],
            "effective_priority": v["user_priority"],
            "source": SOURCE_USER,
            "comment": v.get("comment", "") or "",
        }
    return out


def apply_hybrid(
    rows: Iterable[dict[str, Any]],
    db_path: Path,
) -> list[dict[str, Any]]:
    """Stream-overlay user verdicts onto pre-loaded golden CSV rows.

    Cheaper than ``load_hybrid_labels`` when you already hold the CSV rows
    in memory (the classifier does). Each input row is shallow-copied and
    has ``gold_priority_final`` replaced with the user verdict where
    applicable. A new ``_hybrid_source`` field is added so downstream code
    can audit which rows came from user input vs. derivation.
    """
    user = load_user_verdicts(db_path)
    out: list[dict[str, Any]] = []
    for row in rows:
        key = (row.get("item_key") or "").strip()
        new = dict(row)
        v = user.get(key) if key else None
        if v is not None:
            user_priority = v["user_priority"]
            new["gold_priority_final"] = user_priority
            # Sprint-1: also overwrite the continuous relevance so the
            # regression target reflects the user's verdict, not the
            # stale derivation from emojis/annotations.
            new["gold_inferred_relevance"] = str(
                _PRIORITY_TO_RELEVANCE[user_priority]
            )
            new["_hybrid_source"] = SOURCE_USER
            new["_hybrid_comment"] = v.get("comment", "") or ""
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
    return {
        "total_rows": n_total,
        "user_verdicts": n_user,
        "user_confirmed_derivation": n_user_confirmed,
        "user_overrode_derivation": n_user_changed,
    }
