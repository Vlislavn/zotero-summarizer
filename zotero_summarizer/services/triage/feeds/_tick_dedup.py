"""feeds: content/trash dedup phases of one daemon tick.

Split out of ``_tick_phases`` (file-size compliance + single responsibility):
everything that decides "have we effectively seen this paper already?" — by the
stable GUID of something the user threw away, by DOI/arXiv against earlier
processed rows, and against the Zotero library. ``_tick`` imports these the same
way it imports the other phases; the identity dedup (``prepare_unprocessed``)
stays in ``_tick_phases`` because it is part of the pick/prepare step.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import LOGGER, _triage_conn

# "Thrown away" markers — re-arrivals carrying these GUIDs are suppressed
# forever (the user-requested "trash → never show again").
_TRASH_DECISIONS = (feeds_storage.DECISION_USER_REJECTED,)
_TRASH_OUTCOMES = (feeds_storage.OUTCOME_TRASHED, feeds_storage.OUTCOME_DELETED_ALL)


def _partition_by_content(
    items: list[dict[str, Any]], *, seen_doi: set[str], seen_arxiv: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pure split of items into ``(to_keep, duplicates)`` by normalised DOI/arXiv.

    ``seen_doi``/``seen_arxiv`` are the already-known content keys; an item that
    matches either is a duplicate. The seen-sets grow as we keep items, so a
    second copy of the same paper *within this batch* is also caught. Items with
    neither id are always kept (DOI/arXiv-only — no false positives). Copies the
    caller's sets rather than mutating them.
    """
    seen_d = set(seen_doi)
    seen_a = set(seen_arxiv)
    to_keep: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for item in items:
        doi = normalize_doi(item.get("doi") or "")
        arxiv = normalize_arxiv_id(item.get("arxiv_id") or "")
        if (doi and doi in seen_d) or (arxiv and arxiv in seen_a):
            duplicates.append(item)
            continue
        if doi:
            seen_d.add(doi)
        if arxiv:
            seen_a.add(arxiv)
        to_keep.append(item)
    return to_keep, duplicates


def _partition_by_trashed_guid(
    items: list[dict[str, Any]], *, trashed_guids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pure split into ``(survivors, trash_rearrivals)`` by stable GUID — an item
    whose GUID matches one the user explicitly trashed is a re-arrival of a
    thrown-away paper, even when it carries no DOI/arXiv to content-dedup on."""
    if not trashed_guids:
        return list(items), []
    survivors: list[dict[str, Any]] = []
    rearrivals: list[dict[str, Any]] = []
    for item in items:
        guid = str(item.get("guid") or "").strip()
        if guid and guid in trashed_guids:
            rearrivals.append(item)
        else:
            survivors.append(item)
    return survivors, rearrivals


def dedup_against_processed(
    unprocessed: list[dict[str, Any]], *, tick_id: str, enabled: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split items into ``(to_triage, duplicates)`` by trashed-GUID then DOI/arXiv.

    Identity dedup (:func:`prepare_unprocessed`) only catches the *same* RSS item
    (same ``feed_item_id``); a paper re-arriving under a fresh
    ``(feed_library_id, feed_item_id)`` — Zotero reassigns ids on rollover, or it
    comes from a second feed — slips through. Two guards close that gap:

      * **Trashed-GUID suppression (always on)** — reject any item whose stable
        GUID matches a paper the user threw away (``user_rejected`` from Today,
        or ``trashed``/``deleted_all`` in Zotero). Durable "never show again";
        runs regardless of ``enabled`` and catches id-less items DOI/arXiv can't.
      * **Content dedup (toggle ``enabled``)** — reject, by DOI/arXiv, any item
        already in ``processed_feed_items`` (or earlier in this batch).

    Both are recorded ``rejected_dedup_processed`` (never re-enter triage/Today,
    no LLM call). ``skipped_error`` rows are retryable, excluded from the content
    set so a transient failure can't block a paper.
    """
    with _triage_conn() as conn:
        trashed_guids = feeds_storage.fetch_trashed_guids(
            conn, decisions=_TRASH_DECISIONS, outcomes=_TRASH_OUTCOMES,
        )
    survivors, trash_rearrivals = _partition_by_trashed_guid(
        unprocessed, trashed_guids=trashed_guids,
    )
    for item in trash_rearrivals:
        LOGGER.info(
            "[%s] skip dedup: %r (re-arrival of a paper you trashed)",
            tick_id, (item.get("title") or "")[:60],
        )
    if not enabled:
        return survivors, trash_rearrivals
    with _triage_conn() as conn:
        pairs = feeds_storage.fetch_processed_content_pairs(
            conn, exclude_decisions=(feeds_storage.DECISION_SKIPPED_ERROR,),
        )
    seen_doi = {normalize_doi(doi) for doi, _ in pairs}
    seen_arxiv = {normalize_arxiv_id(arxiv) for _, arxiv in pairs}
    seen_doi.discard("")
    seen_arxiv.discard("")
    to_triage, content_dupes = _partition_by_content(
        survivors, seen_doi=seen_doi, seen_arxiv=seen_arxiv,
    )
    for item in content_dupes:
        LOGGER.info(
            "[%s] skip dedup: %r (duplicate of an already-processed paper)",
            tick_id, (item.get("title") or "")[:60],
        )
    return to_triage, trash_rearrivals + content_dupes


def dedup_against_library(
    unprocessed: list[dict[str, Any]], *, reader: ZoteroReader, tick_id: str, enabled: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split unprocessed items into (to_triage, already_in_library).

    A failed dedup LOOKUP must NOT be read as "not in library" — that would
    re-materialize an existing paper — so the item is skipped this tick and
    retried next tick once the read succeeds.
    """
    if not enabled:
        return list(unprocessed), []
    library_skipped: list[dict[str, Any]] = []
    to_triage: list[dict[str, Any]] = []
    for item in unprocessed:
        doi = (item.get("doi") or "").strip()
        arxiv = (item.get("arxiv_id") or "").strip()
        if not doi and not arxiv:
            to_triage.append(item)
            continue
        try:
            existing = reader.find_by_external_id(doi=doi or None, arxiv_id=arxiv or None)
        except Exception as exc:  # noqa: BLE001 — external Zotero-read boundary
            LOGGER.warning(
                "[%s] dedup lookup failed for %r; skipping this tick: %s",
                tick_id, (item.get("title") or "")[:60], exc,
            )
            continue
        if existing:
            LOGGER.info(
                "[%s] skip dedup: %r (already in library)",
                tick_id, (item.get("title") or "")[:60],
            )
            library_skipped.append(item)
        else:
            to_triage.append(item)
    return to_triage, library_skipped


__all__ = ["dedup_against_processed", "dedup_against_library"]
