"""Single-row lookups against ``processed_feed_items``.

Split out of ``storage/feeds.py`` for file-size compliance (<500 LOC each)
and single-responsibility: pure read helpers that resolve one row by a key.
``feeds.py`` re-exports these so existing
``from zotero_summarizer.storage import feeds; feeds.get_processed_feed_item_by_id``
callers keep working.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def _fetch_one_processed(
    conn: sqlite3.Connection, value: int, label: str, where_sql: str
) -> dict[str, Any] | None:
    """Fetch one ``processed_feed_items`` row by a positive-int key.

    ``None`` when the row is absent (caller's contract: distinguish "not in DB"
    from a hard error). A non-positive ``value`` is a programmer error and raises.
    ``where_sql`` is the trusted, in-source ``WHERE …``/``ORDER BY …`` fragment.
    """
    safe = int(value)
    if safe <= 0:
        raise ValueError(f"{label} must be positive; got {value!r}")
    row = conn.execute(
        f"SELECT * FROM processed_feed_items WHERE {where_sql} LIMIT 1",
        (safe,),
    ).fetchone()
    return dict(row) if row else None


def get_processed_feed_item_by_id(
    conn: sqlite3.Connection,
    feed_item_id: int,
) -> dict[str, Any] | None:
    """Return the most recent processed_feed_items row for a given feed_item_id.

    The golden CSV uses ``feed:<feed_item_id>`` as the row key, dropping the
    library id. Resolving back from CSV to a DB row therefore goes through
    feed_item_id alone. If the same item id appears across multiple feed
    libraries (rare; Zotero reuses ids per library), the newest row wins
    — the older one is from a previous library that has since gone away.
    """
    return _fetch_one_processed(
        conn, feed_item_id, "feed_item_id", "feed_item_id = ? ORDER BY created_at DESC"
    )


def get_processed_feed_item_by_pk(
    conn: sqlite3.Connection,
    pk: int,
) -> dict[str, Any] | None:
    """Return one processed_feed_items row by its primary-key ``id``.

    The daily slate exposes ``SlatePaper.item_id`` = this PK, so a Today
    card verdict resolves the source row through it.
    """
    return _fetch_one_processed(conn, pk, "pk", "id = ?")


def fetch_processed_content_pairs(
    conn: sqlite3.Connection,
    *,
    exclude_decisions: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Raw ``(doi, arxiv_id)`` for every processed row carrying at least one
    external id whose decision is NOT in ``exclude_decisions``.

    The identity dedup (``filter_unprocessed``) only catches the *same* RSS item;
    the same paper re-arriving under a different GUID / from another feed is a
    fresh ``(feed_library_id, feed_item_id)`` and slips through. The triage tick
    set-ifies these pairs (normalised via ``domain.normalize_doi`` /
    ``normalize_arxiv_id`` — the single source of truth for canonicalisation) to
    reject a content duplicate before it re-enters triage. Empty strings stand in
    for the absent member of a pair; the caller discards them.
    """
    where = "(doi IS NOT NULL OR arxiv_id IS NOT NULL)"
    params: list[Any] = []
    if exclude_decisions:
        placeholders = ",".join("?" * len(exclude_decisions))
        where += f" AND decision NOT IN ({placeholders})"
        params.extend(exclude_decisions)
    rows = conn.execute(
        f"SELECT doi, arxiv_id FROM processed_feed_items WHERE {where}",
        params,
    ).fetchall()
    return [(str(r[0] or ""), str(r[1] or "")) for r in rows]


def fetch_trashed_guids(
    conn: sqlite3.Connection,
    *,
    decisions: tuple[str, ...],
    outcomes: tuple[str, ...],
) -> set[str]:
    """GUIDs of papers the user explicitly threw away — the durable key for
    "never show this again".

    A trashed paper re-arrives from the feed under a *fresh*
    ``(feed_library_id, feed_item_id)`` (Zotero reassigns feed-item ids when an
    item rolls over / re-posts / comes from a second feed), so the per-item
    ``feed:<id>`` trash label and the ``user_rejected`` decision both sit on the
    *old* row and miss the re-arrival. DOI/arXiv content-dedup only helps papers
    that carry an external id (journal/news RSS items often carry neither). The
    GUID — the feed item's stable URL/id — survives across re-ingestions, so it
    is the content-addressed key that makes trash memory permanent.

    Returns the set of non-empty GUIDs whose row carries a trashing ``decision``
    (e.g. ``user_rejected`` — trashed from Today) OR a trashing ``final_outcome``
    (e.g. ``trashed`` / ``deleted_all`` — thrown away inside Zotero). Both
    taxonomies are passed in by the caller so this stays pure SQL with no import
    of the constants module.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if decisions:
        placeholders = ",".join("?" * len(decisions))
        clauses.append(f"decision IN ({placeholders})")
        params.extend(decisions)
    if outcomes:
        placeholders = ",".join("?" * len(outcomes))
        clauses.append(f"final_outcome IN ({placeholders})")
        params.extend(outcomes)
    if not clauses:
        return set()
    where = " OR ".join(clauses)
    rows = conn.execute(
        f"SELECT DISTINCT guid FROM processed_feed_items WHERE {where}",
        params,
    ).fetchall()
    return {str(r[0]).strip() for r in rows if str(r[0] or "").strip()}


def fetch_resolved_outcomes(
    conn: sqlite3.Connection,
    *,
    outcomes: tuple[str, ...],
) -> dict[int, str]:
    """``{feed_item_id: final_outcome}`` for materialized rows whose 7-day
    outcome check resolved to one of ``outcomes``.

    The caller passes the outcome subset it considers meaningful (e.g. the
    behavioural taxonomy, excluding ``pending``/``unknown``) so this stays pure
    SQL with no import of the constants module — same contract as
    :func:`fetch_trashed_guids`. Rows with ``feed_item_id <= 0`` are skipped:
    they key their golden row as ``processed:<pk>`` and can't be joined here.
    """
    if not outcomes:
        return {}
    placeholders = ",".join("?" * len(outcomes))
    rows = conn.execute(
        f"""
        SELECT feed_item_id, final_outcome FROM processed_feed_items
        WHERE outcome_detected_at IS NOT NULL
          AND final_outcome IN ({placeholders})
          AND feed_item_id > 0
        """,
        tuple(outcomes),
    ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}
