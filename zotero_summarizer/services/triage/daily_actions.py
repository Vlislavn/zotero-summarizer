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

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.domain import VERDICT_SOURCE_MACHINE_ADD, VERDICT_SOURCE_USER
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services import interaction_log
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.services.library import deep_review, fulltext, review
from zotero_summarizer.services._common import LOGGER, is_app_rss_source
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage.feed_identity import LEGACY_FEED_PREFIX, row_feed_keys
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
    label_verdicts.set_label_verdict(
        _db_path(),
        item_key=item_key,
        original_derived_priority=original_priority,
        user_priority=priority,
        surface=surface,
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


def record_row_outcome(row: dict[str, Any], outcome: str) -> None:
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


def _resolve_collection_name(collection_key: str | None) -> str:
    """Picker key → user-library collection name (default "Inbox" when unset). An
    unknown/foreign key is a 400 — a raw key must never reach the name-based writer,
    which would auto-create a junk collection named after it."""
    if not collection_key:
        return "Inbox"
    name = ZoteroReader(get_settings().zotero_data_dir).collection_name_for_key(collection_key)
    if not name:
        raise APIError(
            error="invalid_collection",
            message=f"no such collection: {collection_key}",
            status_code=400,
        )
    return name


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


def _mark_pending(row: dict[str, Any], reason: str) -> None:
    """Park a row as user-approved-but-not-yet-in-Zotero so the user's next
    'Apply all approved' flushes it (the writer was absent, or the DB stayed
    locked through retries)."""
    _set_decision(row, feeds_storage.DECISION_USER_APPROVED, reason)
    _mark_zotero_sync(row, "pending")
    record_row_outcome(row, feeds_storage.OUTCOME_KEPT_UNREAD_APP)


def _materialize_one(
    row: dict[str, Any], *, writer: Any, used_keys: set[str], collection_name: str,
    materialized: list[tuple[str, str]], reason: str,
    label_priority: str | None = None,
) -> str:
    """Create ONE Zotero item from a feed row + carry its in-place deep review
    onto the new library key. Appends to ``materialized`` for the caller's batch
    post-processing (fulltext + render carry). Raises on Zotero
    failure — the caller decides whether to park the row pending. Records NO
    training label: the caller owns labelling.

    ``label_priority`` stamps the user's ground-truth ``label:<priority>`` tag on
    the new item (verdict-add path only); ``None`` for the machine Add button."""
    new_key = review.materialize_row(
        row, writer=writer, used_keys=used_keys, reason=reason,
        collection_name=collection_name, label_priority=label_priority,
    )
    sfk = str(row.get("stable_feed_key") or "")
    deep_review.copy_review(sfk, new_key)
    materialized.append((sfk, new_key))
    return new_key


def add_to_library(item_ids: list[int], target_collection_key: str | None = None) -> dict[str, Any]:
    """Materialize each selected card into a Zotero collection (default "Inbox") +
    record a positive training label. Returns ``{added, failed_count, failed}``.

    ``target_collection_key`` is the picker's user-library collection key; it is
    resolved (and validated) to a collection name — an unknown key is a 400, never a
    silent junk-collection auto-create."""
    rows = _load_rows(item_ids)
    collection_name = _resolve_collection_name(target_collection_key)
    writer, writer_error = _open_optional_writer()
    used_keys: set[str] = set()
    added = 0
    pending_sync = 0
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
                _mark_pending(row, "today_add_zotero_pending")
                pending_sync += 1
            else:
                try:
                    # Carries the in-place Today review onto the new library key.
                    _materialize_one(
                        row, writer=writer, used_keys=used_keys,
                        collection_name=collection_name,
                        materialized=materialized, reason="today_add",
                    )
                except Exception as exc:
                    _mark_pending(row, "today_add_zotero_pending")
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
    # Auto-fetch OA full text for the just-added papers.
    # Best-effort: the items are already in Zotero, so a fetch failure must NOT fail the
    # add — the bulk "Fetch full text" button can complete it later.
    fulltext = _attach_fulltext_best_effort(materialized)
    # Carry the heavy brief onto the library: a feed paper that already had a render
    # rebuilds under its new Zotero key (after fulltext attach, so the real PDF is present).
    _carry_renders_best_effort(materialized)
    return {
        "added": added, "pending_sync": pending_sync,
        "zotero_sync_error": str(writer_error) if writer_error is not None else None,
        "failed_count": len(failed), "failed": failed[:20],
        "fulltext": fulltext,
    }


def _load_feed_row(item_key: str) -> dict[str, Any] | None:
    """Resolve a feed verdict ``item_key`` (a stable feed key, or a legacy
    ``feed:<feed_item_id>``) to its processed_feed_items row. ``None`` when no
    such row exists."""
    key = str(item_key or "").strip()
    if not key:
        return None
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        row = feeds_storage.get_processed_feed_item_by_stable_key(conn, key)
        if row is None and key.startswith(LEGACY_FEED_PREFIX):
            rest = key[len(LEGACY_FEED_PREFIX):]
            if rest.isdigit():
                row = feeds_storage.get_processed_feed_item_by_id(conn, int(rest))
        return dict(row) if row else None
    finally:
        conn.close()


def _materialized_key_for(row: dict[str, Any]) -> str | None:
    """The Zotero key this feed paper is already materialized under, or ``None``.

    Checks the row itself AND any sibling ``processed_feed_items`` row with the
    same ``stable_feed_key`` — a feed paper can re-arrive as several rows (one
    materialized on an earlier pass, a later one not), so a per-row check would
    miss the existing item and re-add a duplicate."""
    direct = str(row.get("materialized_zotero_key") or "").strip()
    if direct:
        return direct
    sfk = str(row.get("stable_feed_key") or "").strip()
    if not sfk:
        return None
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        sibling = conn.execute(
            "SELECT materialized_zotero_key FROM processed_feed_items "
            "WHERE stable_feed_key = ? AND materialized_zotero_key IS NOT NULL "
            "AND materialized_zotero_key <> '' LIMIT 1",
            (sfk,),
        ).fetchone()
        return str(sibling["materialized_zotero_key"]) if sibling else None
    finally:
        conn.close()


def materialize_feed_verdict(item_key: str, user_priority: str) -> dict[str, Any]:
    """Materialize ONE feed paper (identified by its verdict ``item_key`` /
    stable feed key) into the Zotero "Inbox", as a side-effect of a positive
    verdict set in the Today deep-review. Reuses the add-to-library machinery but
    records NO training label — ``submit_verdict`` already saved the user's fine
    verdict, and re-labelling here would duplicate the golden row and clobber a
    must/could verdict with the generic "add" label.

    ``user_priority`` (the verdict) is stamped on the new Zotero item as its
    ground-truth ``label:<priority>`` tag, in the same lock-tolerant write that
    creates the item — so the user's label lands even though Zotero is open (the
    separate ``zotero_set_label_tag`` refuses then). The user asked: a label set
    during Today review MUST reach Zotero.

    Returns ``{"added": bool, "zotero_key": str | None, "status": str}``.
    Idempotent: an already-materialized paper is a no-op (status
    ``already_in_library``). Zotero failures are the documented add-to-library
    boundary — the row is parked pending and the failure returned as a status
    (``zotero_unavailable`` / ``zotero_pending``), never raised, so the caller
    can report it without blocking the already-durable verdict."""
    row = _load_feed_row(item_key)
    if row is None:
        return {"added": False, "zotero_key": None, "status": "no_feed_row"}
    existing = _materialized_key_for(row)
    if existing:
        return {"added": False, "zotero_key": existing, "status": "already_in_library"}

    writer, _writer_error = _open_optional_writer()
    if writer is None:
        _mark_pending(row, "verdict_add_zotero_pending")
        return {"added": False, "zotero_key": None, "status": "zotero_unavailable"}

    materialized: list[tuple[str, str]] = []
    try:
        new_key = _materialize_one(
            row, writer=writer, used_keys=set(), collection_name="Inbox",
            materialized=materialized, reason="verdict_add",
            label_priority=user_priority,
        )
    except Exception as exc:  # noqa: BLE001 — add-to-library boundary: park pending, report to caller
        _mark_pending(row, "verdict_add_zotero_pending")
        LOGGER.warning("materialize_feed_verdict: Zotero export pending for %s: %s", item_key, exc)
        return {"added": False, "zotero_key": None, "status": "zotero_pending"}

    _attach_fulltext_best_effort(materialized)
    _carry_renders_best_effort(materialized)
    return {"added": True, "zotero_key": new_key, "status": "added"}


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


def _attach_fulltext_best_effort(materialized: list[tuple[str, str]]) -> dict[str, Any]:
    """Attach cached-review or newly acquired OA PDFs. Never fails the Add."""
    if not materialized:
        return {"attached": 0}
    try:
        reader = ZoteroReader(get_settings().zotero_data_dir)
        urls = reader.get_field_values("url")
        dois = reader.get_field_values("DOI")
        items = [
            {
                "item_key": key, "has_pdf": False, "url": urls.get(key, ""),
                "doi": dois.get(key, ""),
                "cached_acquisition": (deep_review.get_cached_review(key) or {}).get("acquired_pdf"),
            }
            for _stable_key, key in materialized
        ]
        return fulltext.fetch_fulltext_for_items(items)
    except Exception as exc:  # noqa: BLE001 — best-effort; the add already succeeded
        LOGGER.exception("add_to_library: full-text fetch failed (non-fatal)")
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
            record_row_outcome(row, feeds_storage.OUTCOME_TRASHED)
            fid = int(row.get("feed_item_id") or 0)
            if fid and is_app_rss_source(row):
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
