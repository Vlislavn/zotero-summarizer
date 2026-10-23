"""Materialize a federated Search candidate into a chosen Zotero collection.

The ONE place the Targeted Search domain writes to Zotero — an EXPLICIT user action
(the "Add to library" button), never triggered by ingested content. It:

  1. resolves + VALIDATES the picker's collection key → a user-library name (an
     unknown key is a 400, never a junk auto-create),
  2. builds a create-item payload FROM the Candidate (not ``to_scoring_dict`` — that
     shape is for the ranker: it drops authors to a comma string and emits ``year``,
     but the writer wants a list + ``publication_date``),
  3. reuses a coverage hit's key, otherwise saves a planned key before calling
     ``apply_feed_materialization``; retries reuse that identity,
  4. stamps ``materialized_zotero_key`` after native return or reuses coverage.
     The existing lock/merge protects these keys within one app process;
     session JSON and native SQLite are not one transaction.
"""
from __future__ import annotations

from html import escape
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search._models import Candidate, ResearchSession

_TAG = "targeted-search"  # provenance: how the item entered the library


def _feed_payload(cand: Candidate) -> dict[str, Any]:
    """Candidate → ``apply_feed_materialization`` payload. ``authors`` stays a LIST
    (the writer's ``_insert_creators`` handles ``list[str]`` directly); ``year`` →
    ``publication_date``; ``venue`` → ``publication_title``. ponytail: item_type is
    ``journalArticle`` for all — always a valid Zotero type; a preprint retype is rare
    and the user can change it."""
    return {
        "title": cand.title or "Untitled",
        "abstract": cand.abstract or "",
        "url": cand.url or "",
        "doi": cand.doi or "",
        "publication_date": str(cand.year) if cand.year else "",
        "publication_title": cand.venue or "",
        "authors": list(cand.authors or []),
        "item_type": "journalArticle",
    }


def _note_html(cand: Candidate, query: str) -> str:
    from zotero_summarizer.services.zotero import pending as pending_service

    brief = (cand.review or {}).get("brief") or {}
    summary = brief.get("tldr") or (cand.abstract or "")[:400]
    marker = pending_service.build_provenance_comment(run_id=f"targeted_search:{cand.candidate_id}",
                                                       source="targeted-search")
    return (f"{marker}<h2>{escape(cand.title or 'Untitled')}</h2>"
            f"<p>Added from Targeted Search — query: {escape(query)}</p><p>{escape(summary)}</p>")


def _resolve_collection_name(collection_key: str | None) -> str:
    if not collection_key:
        return "Inbox"
    name = ZoteroReader(settings().zotero_data_dir).collection_name_for_key(collection_key)
    if not name:
        raise APIError(
            error="invalid_collection",
            message=f"no such collection: {collection_key}",
            status_code=400,
        )
    return name


def materialize_candidate(
    session_id: str, candidate_id: str, collection_key: str | None
) -> dict[str, Any]:
    """File one candidate into the chosen Zotero collection (default Inbox). Explicit
    user action — returns ``{status, zotero_key, collection}``. The session lock
    serializes same-process Adds; a saved plan survives failed finalization so a
    retry can reuse the native writer's existing-key no-op, not create a new key."""
    from zotero_summarizer.services.triage.feeds import _generate_zotero_key

    collection_name = _resolve_collection_name(collection_key)  # validate → fast 400, before the lock
    reused_existing = False

    def _do_write(cand: Candidate, sess: ResearchSession) -> str:
        nonlocal reused_existing
        from zotero_summarizer.services.search.discovery import _freshness, require_current_discovery

        existing_only = bool(sess.discovery and cand.planned_zotero_key and _freshness(sess) != "current")
        if not existing_only:
            require_current_discovery(sess)  # Already-materialized replay never enters do_write.
        if (cand.existing_zotero_key and cand.planned_zotero_key
                and cand.planned_zotero_key != cand.existing_zotero_key):
            raise APIError(
                error="search_write_conflict", status_code=409,
                message="Saved library coverage conflicts with the retained Add identity. "
                        "Reconcile the library before retrying.",
            )
        if cand.existing_zotero_key and not existing_only:
            reused_existing = True
            return cand.existing_zotero_key
        try:
            writer = ZoteroWriter(settings().zotero_data_dir)
        except Exception as exc:  # noqa: BLE001 — Zotero-write is optional; surface as 503, don't 500.
            raise APIError(
                error="zotero_unavailable",
                message="Zotero is not available to write to",
                status_code=503,
            ) from exc
        if not cand.planned_zotero_key:
            cand.planned_zotero_key = _generate_zotero_key(set())
            session_store.save(sess)  # Persist identity before backup or any native write.
        new_key = cand.planned_zotero_key
        try:
            receipt = writer.apply_feed_materialization(
                new_item_key=new_key,
                feed_payload=_feed_payload(cand),
                inbox_collection_name=collection_name,
                tags=[_TAG],
                note_title=f"Targeted Search: {sess.raw_query[:80]}",
                note_html=_note_html(cand, sess.raw_query),
                **({"existing_only": True} if existing_only else {}),
            )
            if existing_only:
                if (type(receipt) is not dict
                        or receipt != {"item_key": new_key, "applied_steps": [], "backup_path": None}):
                    raise APIError("search_write_unconfirmed", "Retained Add was not acknowledged", status_code=409)
                reused_existing = True
        except ZoteroWriteError as exc:
            # Surface the failure without claiming delivery; retain the plan for retry.
            raise APIError(
                error="zotero_locked",
                message="Zotero DB is locked (is Zotero open?) — try again in a moment",
                status_code=503,
            ) from exc
        return new_key

    result = session_store.materialize_once(session_id, candidate_id, _do_write)
    if result is None:
        raise APIError(
            error="not_found", message=f"no such candidate: {candidate_id}", status_code=404
        )
    added, key = result
    if not added or reused_existing:
        return {"status": "already_added", "zotero_key": key}
    return {"status": "added", "zotero_key": key, "collection": collection_name}


__all__ = ["materialize_candidate"]
