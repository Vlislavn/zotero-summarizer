"""Sync ML-relevance band tags onto Zotero library items.

Writes a distinct ``zs:rel/<band>`` tag (must/should/could/dont_read) derived
from the gate's cached relevance score, so the user can filter their library by
ML relevance directly in Zotero. Mutually exclusive within the ``zs:rel/*``
namespace and **never** touches priority (``zs:<band>``) or emoji feedback tags
— manual decisions are preserved.

One backup for the whole batch (``apply_changes(..., create_backup=True)``), per
the project rule that Zotero writes back up first. Idempotent: items already
carrying the correct rel-tag are skipped.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.domain import apply_prestige_floor, score_to_priority
from zotero_summarizer.services.library import reading_queue
from zotero_summarizer.services.zotero.pending import build_rel_tag_change
from zotero_summarizer.services.zotero.zotero import (
    get_zotero_reader_or_raise,
    get_zotero_writer_or_raise,
)


def sync_rel_tags(*, force: bool = False) -> dict[str, Any]:
    """Apply ``zs:rel/<band>`` tags to scored library items.

    Returns ``{tagged, by_band, backup_path, failed_count}`` on success, or a
    ``{requires_force: True}`` notice when Zotero is running (writing while the
    connector is live can corrupt the DB) unless ``force`` is set.
    """
    scores = reading_queue.read_score_cache()
    if not scores:
        return {
            "tagged": 0, "by_band": {}, "backup_path": None,
            "message": "No relevance scores cached — Rescore the library first.",
        }

    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running() and not force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    # Quality floor (median of the library's KNOWN prestige) so the top rel-tags
    # reserve must/should for high-prestige work; unknown prestige → kept.
    floor = reading_queue.prestige_floor([(e["prestige"], e["prestige_known"]) for e in scores.values()])

    items = reader.get_all_items().get("items", [])  # whole library (annotations excluded)
    changes: list[dict[str, Any]] = []
    by_band: dict[str, int] = {}
    for it in items:
        key = it.get("item_key")
        entry = scores.get(str(key))
        if entry is None:
            continue
        band = apply_prestige_floor(
            score_to_priority(entry["relevance"]), entry["prestige"],
            prestige_known=entry["prestige_known"], floor=floor,
        )
        current_tags = [str(t).strip() for t in (it.get("tags") or []) if str(t).strip()]
        payload = build_rel_tag_change(current_tags, band)
        if not payload["add_tags"] and not payload["remove_tags"]:
            continue  # already correct → idempotent
        changes.append(
            {"id": 0, "item_key": key, "change_type": "tag_changes", "payload_json": payload}
        )
        by_band[band] = by_band.get(band, 0) + 1

    if not changes:
        return {
            "tagged": 0, "by_band": {}, "backup_path": None,
            "message": "All relevance tags already up to date.",
        }

    result = writer.apply_changes(changes, True)  # True = backup first
    return {
        "tagged": len(result.get("applied_ids") or []),
        "by_band": by_band,
        "backup_path": result.get("backup_path"),
        "failed_count": len(result.get("failed") or []),
    }


# Zotero's Call Number is a free-text, SORTABLE column (unused for journal
# articles), so it's the place to stamp our rank. A short marker prefix keeps the
# value identifiable as ours and zero-padding makes string-sort == rank order.
_RANK_FIELD = "callNumber"
_RANK_PREFIX = "zr"


def sync_score_ranks(*, force: bool = False) -> dict[str, Any]:
    """Stamp a whole-library RANK into every paper's Zotero Call Number
    (``zr0001``, ``zr0002``, …), so sorting that column in Zotero reproduces the
    app's order across the ENTIRE library. Scorable papers rank on top by the
    goal-blended relevance order; genuinely no-abstract papers sink to the bottom
    (still numbered, so everything stays sortable). Run a Rescore first to build
    the global score cache — this step only writes.

    Backup-first; ``set_field`` overwrites only the rank field (Call Number),
    never tags/notes/other fields. Re-run after a Rescore to refresh ranks.
    Returns ``{ranked, scored, unscored, field, backup_path, failed_count}`` or a
    ``{requires_force: True}`` notice when Zotero is running."""
    scores = reading_queue.read_score_cache()  # global cache (whole library)
    if not scores:
        return {
            "ranked": 0, "scored": 0, "unscored": 0, "field": _RANK_FIELD,
            "backup_path": None,
            "message": "No scored items — Rescore the whole library first.",
        }

    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running() and not force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    items = reader.get_all_items().get("items", [])  # ALL papers (annotations excluded)
    goal_sims = reading_queue._goal_affinity([str(it["item_key"]) for it in items])
    records: list[dict[str, Any]] = []
    for it in items:
        key = str(it["item_key"])
        entry = scores.get(key)
        records.append({
            "item_key": key,
            "relevance_score": entry["relevance"] if entry else None,  # None → bottom
            "goal_sim": goal_sims.get(key),
            "date_added": it.get("date_added") or "",
        })
    # Global order: scorable papers first by the goal-blend, no-abstract (None
    # relevance) papers sink to the bottom by date. NO dedup — every item must get
    # a number so the whole library is sortable in Zotero.
    reading_queue._blended_sort(records)
    scored = sum(1 for r in records if r["relevance_score"] is not None)

    changes = [
        {
            "id": 0,
            "item_key": r["item_key"],
            "change_type": "set_field",
            "payload_json": {"field": _RANK_FIELD, "value": f"{_RANK_PREFIX}{rank:04d}"},
        }
        for rank, r in enumerate(records, start=1)
    ]
    result = writer.apply_changes(changes, True)  # True = backup first
    return {
        "ranked": len(result.get("applied_ids") or []),
        "scored": scored,
        "unscored": len(records) - scored,
        "field": _RANK_FIELD,
        "backup_path": result.get("backup_path"),
        "failed_count": len(result.get("failed") or []),
    }
