"""Explicit ``label:<priority>`` tag — the Zotero-native ground truth.

The user's deliberate reading verdict lives as a Zotero tag ``label:<priority>``
(see :data:`zotero_summarizer.domain.LABEL_TAG_PREFIX`). This module owns the
read side of that tag: detecting it on an item, and reconciling it into the
``label_verdicts`` store so the two never drift.

The label is the **highest-precedence** signal: when present on a library item it
overrides emoji/annotation/note engagement scoring in
:func:`services.golden.goldenset._infer_label`. :func:`detect_label` mirrors the
shape of :func:`services.emoji_signals.detect_signals` so the two taxonomies read
alike.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, NamedTuple

from zotero_summarizer.domain import (
    LABEL_TAG_PREFIX,
    READING_PRIORITY_SORT_RANK,
    priority_from_label_tag,
)

# SQLite default parameter cap is 999; chunk key lookups well under it.
_KEY_BATCH = 400

# Marker stored in ``label_verdicts.original_derived_priority`` for verdicts that
# ORIGINATED from a Zotero ``label:*`` tag (created by reconcile). ONLY these are
# governed by the tag and may be retracted when it's removed — a verdict typed in
# the Annotate UI carries its derived original and is never auto-deleted, so the
# user's in-app labels can't be wiped just because they lack a Zotero tag.
ZOTERO_LABEL_ORIGIN = "zotero_label"


class ReconcileCounts(NamedTuple):
    """Result of one ``reconcile_label_verdicts`` pass.

    ``synced``  — verdicts created or updated to match a Zotero ``label:*`` tag.
    ``changed`` — the subset of ``synced`` whose value *differed* from the cached
                  verdict (you re-labelled in Zotero — a drift signal).
    ``removed`` — verdicts retracted because the ``label:*`` tag was deleted in
                  Zotero (you changed your mind by removing the tag).
    """
    synced: int
    changed: int
    removed: int


def detect_label(tags: list[str]) -> str | None:
    """Return the explicit reading priority from a ``label:<priority>`` tag.

    ``None`` when no recognised label tag is present. If more than one label
    tag is somehow set (the write path keeps them mutually exclusive, but a
    hand-edited library can drift), the highest-priority one wins so a stray
    leftover never silently downgrades a deliberate ``label:must_read``.
    """
    found: list[str] = []
    for tag in tags:
        priority = priority_from_label_tag(tag)
        if priority is not None:
            found.append(priority)
    if not found:
        return None
    return max(found, key=lambda p: READING_PRIORITY_SORT_RANK[p])


def reconcile_label_verdicts(
    samples: Iterable[Any], zotero_db_path: Path, triage_db_path: Path,
) -> ReconcileCounts:
    """Two-way sync of Zotero ``label:<priority>`` tags into ``label_verdicts``.

    The label tag is the source of truth (user-confirmed: label in Zotero OR the
    app, Zotero reconciles). ``label_verdicts`` — read by the Annotate UI and the
    ``hybrid_gt`` training overlay — is kept in step so a stale in-app verdict can
    never override the Zotero label.

    1. **Upsert** from ``samples`` (the export's
       :class:`~services.golden.goldenset.GoldenSample` rows, duck-typed to avoid
       a circular import — each needs ``gold_signal_tier``, ``item_key``,
       ``gold_priority_inferred``): an item carrying a ``label:*`` tag (tier
       ``user_label``) writes/updates its verdict. Idempotent (in-sync rows skip).
    2. **Retract** (user-confirmed 2026-06): a verdict whose ``label:*`` tag was
       *deleted* in Zotero is dropped — but SAFELY: only when the item is present,
       live (libraryID=1, not trashed) and carries no ``label:*`` tag. A missing /
       unreadable / trashed item is left alone (a transient lock must never lose a
       verdict). feed:/note: verdicts (no Zotero item to tag) are never touched.
    """
    from zotero_summarizer.storage import repositories

    synced = 0
    changed = 0
    for sample in samples:
        if sample.gold_signal_tier != "user_label":
            continue
        existing = repositories.get_label_verdict(triage_db_path, sample.item_key)
        if existing is not None and existing["user_priority"] == sample.gold_priority_inferred:
            continue
        if existing is not None:
            changed += 1
        repositories.insert_or_update_label_verdict(
            triage_db_path,
            item_key=sample.item_key,
            original_derived_priority=(
                existing["original_derived_priority"] if existing is not None else ZOTERO_LABEL_ORIGIN
            ),
            user_priority=sample.gold_priority_inferred,
            comment=existing["comment"] if existing is not None else "",
        )
        synced += 1

    removed = _retract_removed_labels(zotero_db_path, triage_db_path)
    return ReconcileCounts(synced=synced, changed=changed, removed=removed)


def _retract_removed_labels(zotero_db_path: Path, triage_db_path: Path) -> int:
    """Delete verdicts whose ``label:*`` tag was removed in Zotero — safely.

    Scoped to **tag-sourced** verdicts only (``original_derived_priority ==
    ZOTERO_LABEL_ORIGIN`` — created by reconcile FROM a Zotero tag). A verdict
    typed in the Annotate UI carries its derived original and is NEVER deleted
    here, so the user's hundreds of in-app verdicts can't be wiped just because
    they were never pushed out as Zotero tags. Within that scope, retract only
    when the item is present, live (libraryID=1, not trashed) and tag-free; a
    missing/unreadable/trashed item is left alone. feed:/note: keys are skipped.
    """
    from zotero_summarizer.services.library.review_detail import (
        SOURCE_FEED,
        SOURCE_NOTE,
        classify_item_key,
    )
    from zotero_summarizer.storage import repositories

    verdicts = repositories.list_label_verdicts(triage_db_path, limit=5000)
    tag_sourced = {
        v["item_key"]
        for v in verdicts
        if v.get("original_derived_priority") == ZOTERO_LABEL_ORIGIN
        and classify_item_key(v["item_key"]) not in (SOURCE_FEED, SOURCE_NOTE)
    }
    if not tag_sourced:
        return 0

    live_has_label = _live_label_state(zotero_db_path, tag_sourced)
    removed = 0
    for key in tag_sourced:
        has_label = live_has_label.get(key)
        if has_label is None or has_label:
            # missing / unreadable / trashed (keep — safe), or tag still present.
            continue
        if repositories.delete_label_verdict(triage_db_path, key):
            removed += 1
    return removed


def _live_label_state(zotero_db_path: Path, keys: set[str]) -> dict[str, bool]:
    """``{item_key: has_label_tag}`` for keys that are PRESENT + live in Zotero
    (libraryID=1, not trashed). Keys absent from the result are missing /
    unreadable / trashed — callers must NOT retract those."""
    from zotero_summarizer.services._common import connect_sqlite_ro

    out: dict[str, bool] = {}
    if not keys:
        return out
    ordered = sorted(keys)
    conn = connect_sqlite_ro(zotero_db_path)
    try:
        for start in range(0, len(ordered), _KEY_BATCH):
            batch = ordered[start:start + _KEY_BATCH]
            placeholders = ",".join("?" * len(batch))
            sql = (
                "SELECT i.key, "
                f"MAX(CASE WHEN t.name LIKE '{LABEL_TAG_PREFIX}%' THEN 1 ELSE 0 END) "
                "FROM items i "
                "LEFT JOIN itemTags it ON it.itemID = i.itemID "
                "LEFT JOIN tags t ON t.tagID = it.tagID "
                f"WHERE i.libraryID = 1 AND i.key IN ({placeholders}) "
                "AND NOT EXISTS (SELECT 1 FROM deletedItems d WHERE d.itemID = i.itemID) "
                "GROUP BY i.key"
            )
            for row in conn.execute(sql, batch):
                out[str(row[0])] = bool(row[1])
    finally:
        conn.close()
    return out
