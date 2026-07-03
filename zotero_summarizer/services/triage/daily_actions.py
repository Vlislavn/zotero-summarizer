"""Stage-1 (Today) keep/trash actions for the two-stage reading flow.

``add_to_library`` materializes selected Today cards into the Zotero "Inbox"
collection AND records a positive training label. ``trash`` records a strong
negative training label and marks the feed items read. Both are batch
(multi-select), idempotent, and report per-row failures rather than aborting
the whole batch (the same batch contract as
``services.review.apply_all_approved``).

The fine must/should/could/don't priority is NOT chosen here — the user makes
a coarse keep/trash call before reading. Stage-2 annotation refines it later
(manual-wins, already shipped).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_MACHINE_ADD, VERDICT_SOURCE_USER
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services import interaction_log
from zotero_summarizer.services.library import deep_review, fulltext, review
from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage.feed_identity import row_feed_keys
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage import rss as rss_storage

# Provisional positive label for "add to library": the user signalled the
# paper is worth reading, but hasn't read it yet. Stage-2 annotation overrides
# this (the verdict overlay makes the manual label win on retrain).
_ADD_PRIORITY = "should_read"


def _db_path():
    return get_settings().triage_db_path


def _load_rows(item_ids: list[int]) -> list[dict[str, Any]]:
    """Fetch processed_feed_items rows for the given PKs (missing PKs skipped)."""
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        rows: list[dict[str, Any]] = []
        for pk in item_ids:
            row = feeds_storage.get_processed_feed_item_by_pk(conn, int(pk))
            if row is not None:
                rows.append(dict(row))
        return rows
    finally:
        conn.close()


def _golden_key(row: dict[str, Any]) -> str:
    return row_feed_keys(row)[0]


def _record_label(
    row: dict[str, Any], priority: str, note: str, *,
    signal_tier: str = "feed_user_label", original_priority: str | None = None,
    source: str = VERDICT_SOURCE_USER, surface: str,
) -> None:
    """Write the training label two ways: golden CSV (for retrain) + the
    label_verdicts overlay (so it persists, wins, and excludes the card from
    the slate via the shipped handled-paper filter).

    ``signal_tier`` sets the golden row's training weight tier — `feed_interest`
    for the soft pre-read "Add to library" signal, `feed_user_label` (default)
    for a confident decision like trash.

    ``source`` marks verdict provenance: ``machine_add`` for the provisional
    pre-read "Add" verdict (superseded by the 7-day materialization outcome at
    train time — see ``services.golden.hybrid_gt``), ``user`` (default) for a
    deliberate decision like trash.

    ``original_priority`` records the gate/model's derived priority on the
    verdict overlay. Callers that mutate ``row["reading_priority"]`` before
    labelling (e.g. ``add_to_library``) MUST pass the pre-mutation value here,
    or the overlay would store the user's new label as the "original"."""
    if original_priority is None:
        original_priority = (row.get("reading_priority") or "").strip() or "unknown"
    review.append_to_golden(row, label=priority, note=note, signal_tier=signal_tier)
    item_key = _golden_key(row)
    repositories.insert_or_update_label_verdict(
        _db_path(),
        item_key=item_key,
        original_derived_priority=original_priority,
        user_priority=priority,
        comment=note,
        source=source,
    )
    interaction_log.log_feed_decision(
        row=row, item_key=item_key, surface=surface, source=source,
        model_priority=original_priority, comment=note,
        human={"kind": "keep" if priority != "dont_read" else "trash", "value": priority},
    )


def _set_decision(row: dict[str, Any], decision: str, reason: str) -> None:
    conn = sqlite3.connect(str(_db_path()))
    try:
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=decision,
            decision_reason=reason,
        )
        conn.commit()
    finally:
        conn.close()


def _mark_zotero_sync(row: dict[str, Any], status: str) -> None:
    conn = sqlite3.connect(str(_db_path()))
    try:
        feeds_storage.record_zotero_sync_status(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            status=status,
        )
        conn.commit()
    finally:
        conn.close()


def _record_app_outcome(row: dict[str, Any], outcome: str) -> None:
    conn = sqlite3.connect(str(_db_path()))
    try:
        feeds_storage.record_app_outcome(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            final_outcome=outcome,
            signal_weight=feeds_storage.OUTCOME_WEIGHT[outcome],
        )
        conn.commit()
    finally:
        conn.close()


def _open_optional_writer() -> tuple[Any | None, Exception | None]:
    try:
        return ZoteroWriter(get_settings().zotero_data_dir), None
    except Exception as exc:  # noqa: BLE001 - Zotero is an optional sync target.
        LOGGER.warning("Zotero writer unavailable; local RSS action will continue: %s", exc)
        return None, exc


def _is_app_rss_row(row: dict[str, Any]) -> bool:
    source = str(row.get("source_type") or row.get("source") or "").strip()
    return source == "app_rss"


def _mark_app_rss_rows_read(rows: list[dict[str, Any]]) -> int:
    item_ids = [int(row.get("feed_item_id") or 0) for row in rows]
    conn = sqlite3.connect(str(_db_path()))
    try:
        feeds_storage.init_feeds_schema(conn)
        marked = rss_storage.mark_rss_items_read(conn, item_ids)
        for row in rows:
            feeds_storage.record_read_marked(
                conn,
                feed_library_id=int(row["feed_library_id"]),
                feed_item_id=int(row["feed_item_id"]),
            )
        conn.commit()
        return marked
    finally:
        conn.close()


def add_to_library(item_ids: list[int]) -> dict[str, Any]:
    """Materialize each selected card into the Zotero Inbox + record a positive
    training label. Returns ``{added, failed_count, failed}``."""
    rows = _load_rows(item_ids)
    writer, writer_error = _open_optional_writer()
    used_keys: set[str] = set()
    added = 0
    pending_sync = 0
    new_keys: list[str] = []
    materialized: list[tuple[str, str]] = []  # (stable_feed_key, new_zotero_key) for render carry
    failed: list[dict[str, Any]] = []
    for row in rows:
        try:
            if row.get("materialized_zotero_key"):
                LOGGER.info("add_to_library: skipping already-materialized row id=%s", row.get("id"))
                continue
            # Capture the gate/model's derived priority BEFORE overriding it, so
            # the verdict overlay records the original (e.g. "dont_read"), not the
            # "add" label we're about to write below.
            original_priority = (row.get("reading_priority") or "").strip() or "unknown"
            # Gate-rejected rows carry reading_priority="dont_read"; override it
            # so the Zotero tag reflects the user's positive "add" intent, not the
            # gate's verdict.
            row["reading_priority"] = _ADD_PRIORITY
            # Soft, low-weight training signal: "Add" is pre-read interest, not
            # endorsement — feed_interest → WEIGHT_INTEREST (0.3). A later read +
            # label (or Zotero engagement) on the materialized library item
            # carries full weight and dominates this.
            _record_label(
                row, _ADD_PRIORITY, "added from Today",
                signal_tier="feed_interest", original_priority=original_priority,
                source=VERDICT_SOURCE_MACHINE_ADD, surface="today_keep",
            )
            if writer is None:
                _set_decision(row, feeds_storage.DECISION_USER_APPROVED, "today_add_zotero_pending")
                _mark_zotero_sync(row, "pending")
                _record_app_outcome(row, feeds_storage.OUTCOME_KEPT_UNREAD_APP)
                pending_sync += 1
            else:
                try:
                    new_key = review.materialize_row(row, writer=writer, used_keys=used_keys, reason="today_add")
                    new_keys.append(new_key)
                    # Carry the in-place Today review (cached under stable_feed_key) onto
                    # the new library key so the deep review persists into the library.
                    sfk = str(row.get("stable_feed_key") or "")
                    deep_review.copy_review(sfk, new_key)
                    materialized.append((sfk, new_key))
                except Exception as exc:
                    _set_decision(row, feeds_storage.DECISION_USER_APPROVED, "today_add_zotero_pending")
                    _mark_zotero_sync(row, "pending")
                    _record_app_outcome(row, feeds_storage.OUTCOME_KEPT_UNREAD_APP)
                    pending_sync += 1
                    LOGGER.warning(
                        "add_to_library: Zotero export pending for row id=%s: %s",
                        row.get("id"), exc,
                    )
            added += 1
        except Exception as exc:
            # Batch contract: a Zotero-locked / bad row must not strand the
            # rest of the user's selection. Surface per-row failures instead.
            LOGGER.exception("add_to_library failed for row id=%s", row.get("id"))
            failed.append({
                "id": row.get("id"),
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })
    # Auto-fetch arXiv full text for the just-added papers (user-requested, 2026-06).
    # Best-effort: the items are already in Zotero, so a fetch failure must NOT fail the
    # add — the bulk "Fetch full text" button can complete it later.
    fulltext = _attach_fulltext_best_effort(new_keys)
    # Carry the heavy brief onto the library: a feed paper that already had a render
    # rebuilds under its new Zotero key (after fulltext attach, so the real PDF is present).
    _carry_renders_best_effort(materialized)
    return {
        "added": added, "pending_sync": pending_sync,
        "zotero_sync_error": str(writer_error) if writer_error is not None else None,
        "failed_count": len(failed), "failed": failed[:20],
        "fulltext": fulltext,
    }


def _carry_renders_best_effort(pairs: list[tuple[str, str]]) -> None:
    """For each ``(stable_feed_key, new_zotero_key)`` whose feed paper already had a
    COMPLETED in-place render, rebuild the brief under the new library key — so the library
    opens with the same brief. REBUILD (not copy): the digest is already carried by
    ``deep_review.copy_review``, and the renderer reads the real Zotero PDF so artifact paths
    are correct. Best-effort (user-requested persistence): a render failure never fails the
    add. ``start_build`` is async (its own pool + single-flight), so this does not block."""
    from zotero_summarizer.services.library import paper_render

    for stable_feed_key, new_key in pairs:
        if not stable_feed_key:
            continue
        try:
            state = paper_render._read_state(stable_feed_key)
            if state is not None and state.get("status") == "completed":
                paper_render.start_build(new_key, allow_acquire_missing=True)
        except Exception:  # noqa: BLE001 — best-effort render carry; never fail the add
            LOGGER.warning("add_to_library: render carry failed for %s", new_key, exc_info=True)


def _attach_fulltext_best_effort(new_keys: list[str]) -> dict[str, Any]:
    """Fetch + attach arXiv PDFs for freshly-materialized items. Never raises —
    a failure here is non-fatal to the add (documented best-effort boundary)."""
    if not new_keys:
        return {"attached": 0}
    try:
        reader = ZoteroReader(get_settings().zotero_data_dir)
        urls = reader.get_field_values("url")
        dois = reader.get_field_values("DOI")
        items = [
            {"item_key": k, "has_pdf": False, "url": urls.get(k, ""), "doi": dois.get(k, "")}
            for k in new_keys
        ]
        res = fulltext.fetch_fulltext_for_items(items)
        return {"attached": res.get("attached", 0), "no_arxiv": res.get("no_arxiv", 0),
                "failed": res.get("failed_count", 0)}
    except Exception as exc:  # noqa: BLE001 — best-effort; the add already succeeded
        LOGGER.exception("add_to_library: arXiv full-text fetch failed (non-fatal)")
        return {"attached": 0, "error": f"{type(exc).__name__}: {exc}"}


def trash(item_ids: list[int]) -> dict[str, Any]:
    """Record a strong negative (dont_read) training label for each selected
    card, flip it to user_rejected, and mark the feed items read. Returns
    ``{trashed, marked_read, failed_count, failed}``."""
    rows = _load_rows(item_ids)
    writer, writer_error = _open_optional_writer()
    trashed = 0
    failed: list[dict[str, Any]] = []
    zotero_read_ids: list[int] = []
    app_rss_rows: list[dict[str, Any]] = []
    for row in rows:
        try:
            _record_label(row, "dont_read", "trashed from Today", surface="today_trash")
            _set_decision(row, feeds_storage.DECISION_USER_REJECTED, "trashed_from_today")
            _record_app_outcome(row, feeds_storage.OUTCOME_TRASHED)
            fid = int(row.get("feed_item_id") or 0)
            if fid and _is_app_rss_row(row):
                app_rss_rows.append(row)
            elif fid:
                zotero_read_ids.append(fid)
            trashed += 1
        except Exception as exc:
            # Batch contract: per-row failure is reported, not fatal.
            LOGGER.exception("trash failed for row id=%s", row.get("id"))
            failed.append({
                "id": row.get("id"),
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })
    # The labels above are the source of truth and are already committed. Marking
    # the feed items read in Zotero is a best-effort convenience (its own docstring
    # says so) — if Zotero holds the DB lock it must NOT 500 the whole batch and
    # leave the user thinking the trash failed when the labels actually saved.
    marked = 0
    marked_read_error: str | None = None
    if app_rss_rows:
        try:
            marked += _mark_app_rss_rows_read(app_rss_rows)
        except Exception as exc:  # noqa: BLE001 - local read marking is best-effort.
            LOGGER.warning("trash: app rss mark read failed (labels already saved): %s", exc)
            marked_read_error = str(exc)
    if zotero_read_ids and writer is not None:
        try:
            marked += writer.mark_feed_items_read(zotero_read_ids)
        except Exception as exc:
            LOGGER.warning("trash: mark_feed_items_read failed (labels already saved): %s", exc)
            marked_read_error = str(exc)
    elif zotero_read_ids and writer_error is not None:
        marked_read_error = str(writer_error)
    return {
        "trashed": trashed,
        "marked_read": marked,
        "marked_read_error": marked_read_error,
        "failed_count": len(failed),
        "failed": failed[:20],
    }
