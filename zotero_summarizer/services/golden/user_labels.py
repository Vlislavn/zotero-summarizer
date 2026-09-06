"""Explicit ``label:<priority>`` tag — Zotero's synced current-label representation.

The app commits a deliberate verdict to ``label_verdicts`` first and mirrors a
``label:<priority>`` tag to Zotero when possible. This module owns the reverse
path: detecting a direct Zotero/iPad edit and reconciling it into the app's
current-state store while ``interaction-events.jsonl`` keeps the trajectory.

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
    VERDICT_SOURCE_USER,
    priority_from_label_tag,
)
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.storage import label_mirrors

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

    A direct Zotero edit wins when reconciliation observes it. ``label_verdicts``
    is the operational current-state cache read by Annotate and ``hybrid_gt``;
    the app-owned interaction log is the durable decision trajectory. This is a
    bidirectional current-label bridge, not a claim that either mirror contains
    the other's full history.

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
    from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations

    run_migrations(triage_db_path, "triage", TRIAGE_MIGRATIONS)
    synced = 0
    changed = 0
    states = label_mirrors.states(triage_db_path)
    samples = list(samples)
    live = _live_labels(zotero_db_path, {sample.item_key for sample in samples if sample.gold_signal_tier == "user_label"})
    retracted = _retracted_priorities(states, zotero_db_path, triage_db_path)
    for sample in samples:
        if sample.gold_signal_tier != "user_label":
            continue
        priority = retracted.get(sample.item_key, detect_label(live.get(sample.item_key, [])))
        if priority is None:
            continue
        existing = repositories.get_label_verdict(triage_db_path, sample.item_key)
        if (
            existing is not None
            and existing["user_priority"] == priority
            and existing.get("source") == VERDICT_SOURCE_USER
        ):
            continue
        row_id = label_verdicts.set_label_verdict(
            triage_db_path,
            item_key=sample.item_key,
            original_derived_priority=ZOTERO_LABEL_ORIGIN,
            user_priority=priority,
            surface="zotero_reconcile",
            event_source="zotero",
            comment=existing["comment"] if existing is not None else "",
            transition_comment="",
            history_known=existing is not None,
            expected_revision=states.get(sample.item_key, {}).get("revision", 0),
        )
        if row_id is None:
            continue
        if existing is not None and existing["user_priority"] != priority:
            changed += 1
        synced += 1

    removed = _retract_removed_labels(zotero_db_path, triage_db_path)
    return ReconcileCounts(synced=synced, changed=changed, removed=removed)


def _retracted_priorities(states: dict[str, dict], zotero_db_path: Path, triage_db_path: Path) -> dict[str, str | None]:
    rows = [row for row in states.values() if row["value"] is None]
    live = _live_labels(zotero_db_path, {row["target_key"] for row in rows if row["target_key"]})
    _retry_retractions([row for row in rows if row["target_key"] in live], zotero_db_path, triage_db_path)
    priorities = {}
    for row in rows:
        key = row["target_key"]
        if key is None:
            continue
        priority = detect_label(live.get(key, []))
        if not row["mirrored"]:
            if key in live and priority is None:
                with label_mirrors.current_label(triage_db_path, row["item_key"], revision=row["revision"]):
                    pass  # Live Zotero already has no recognized label to remove.
            priority = None
        priorities[key] = priority
    return priorities


def _retry_retractions(rows: list[dict], zotero_db_path: Path, triage_db_path: Path) -> None:
    from zotero_summarizer.services.golden.verdict_effects import mirror_current_verdict
    from zotero_summarizer.services.zotero import zotero

    pending = [row for row in rows if not row["mirrored"] and row["target_key"]]
    if not pending:
        return
    try:
        reader = zotero.get_zotero_reader_or_raise()
        writer = zotero.get_zotero_writer_or_raise()
    except APIError as exc:
        # A read-only export without configured Zotero must not manufacture a
        # writer. The durable intent remains pending for the configured app.
        if exc.error != "zotero_unavailable":
            raise
        return
    if (reader.db_path.resolve() != zotero_db_path.resolve()
            or writer.db_path.resolve() != zotero_db_path.resolve()
            or writer.is_connector_running()):
        return
    for row in pending:
        mirror_current_verdict(triage_db_path, row["item_key"])


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

    states = label_mirrors.states(triage_db_path)
    verdicts = repositories.list_all_label_verdicts(triage_db_path)
    tag_sourced = {
        v["item_key"]: v
        for v in verdicts
        if v.get("original_derived_priority") == ZOTERO_LABEL_ORIGIN
        and classify_item_key(v["item_key"]) not in (SOURCE_FEED, SOURCE_NOTE)
    }
    if not tag_sourced:
        return 0

    live_has_label = _live_labels(zotero_db_path, tag_sourced)
    removed = 0
    for key, _prior in tag_sourced.items():
        current = states.get(key, {})
        if current.get("value") is not None and current.get("item_key") != key:
            continue  # A newer explicit feed-key verdict owns this materialized paper.
        has_label = live_has_label.get(key)
        if has_label is None or has_label:
            # missing / unreadable / trashed (keep — safe), or tag still present.
            continue
        if label_verdicts.retract_label_verdict(
            triage_db_path, item_key=key, surface="zotero_reconcile", event_source="zotero",
            expected_revision=current.get("revision", 0),
        ):
            removed += 1
    return removed


def _live_labels(zotero_db_path: Path, keys: set[str]) -> dict[str, list[str]]:
    """``{item_key: label_tags}`` for keys that are PRESENT + live in Zotero
    (libraryID=1, not trashed). Keys absent from the result are missing /
    unreadable / trashed — callers must NOT retract those."""
    from zotero_summarizer.services._common import connect_sqlite_ro

    out: dict[str, list[str]] = {}
    if not keys:
        return out
    ordered = sorted(keys)
    conn = connect_sqlite_ro(zotero_db_path)
    try:
        for start in range(0, len(ordered), _KEY_BATCH):
            batch = ordered[start:start + _KEY_BATCH]
            placeholders = ",".join("?" * len(batch))
            sql = (
                "SELECT i.key, t.name "
                "FROM items i "
                "LEFT JOIN itemTags it ON it.itemID = i.itemID "
                f"LEFT JOIN tags t ON t.tagID = it.tagID AND t.name LIKE '{LABEL_TAG_PREFIX}%' "
                f"WHERE i.libraryID = 1 AND i.key IN ({placeholders}) "
                "AND NOT EXISTS (SELECT 1 FROM deletedItems d WHERE d.itemID = i.itemID) "
            )
            for row in conn.execute(sql, batch):
                tags = out.setdefault(str(row[0]), [])
                if row[1] is not None:
                    tags.append(str(row[1]))
    finally:
        conn.close()
    return out
