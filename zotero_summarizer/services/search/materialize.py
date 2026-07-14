"""Materialize a federated Search candidate into a chosen Zotero collection.

The ONE place the Targeted Search domain writes to Zotero — an EXPLICIT user action
(the "Add to library" button), never triggered by ingested content. It:

  1. resolves + VALIDATES the picker's collection key → a user-library name (an
     unknown key is a 400, never a junk auto-create),
  2. builds a create-item payload FROM the Candidate (not ``to_scoring_dict`` — that
     shape is for the ranker: it drops authors to a comma string and emits ``year``,
     but the writer wants a list + ``publication_date``),
  3. writes atomically via ``apply_feed_materialization`` (bypasses the pending queue
     for a new item; retries on a locked DB — no force handshake needed),
  4. stamps ``materialized_zotero_key`` back on the session under its lock, so a
     concurrent auto-review can't lose the stamp.
"""
from __future__ import annotations

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
    return pending_service.build_triage_note_html(
        title=cand.title or "",
        summary=f"Added from Targeted Search — query: {query}\n\n{summary}",
        is_black_swan=False,
        surprise_score=None,
        run_id=f"targeted_search:{cand.candidate_id}",
    )


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
    user action — returns ``{status, zotero_key, collection}``. Idempotent AND
    concurrency-safe: the check-write-stamp runs under the session lock via
    ``session_store.materialize_once``, so two simultaneous "Add to library" clicks on
    the same candidate write to Zotero exactly once."""
    from zotero_summarizer.services.triage.feeds import _generate_zotero_key

    collection_name = _resolve_collection_name(collection_key)  # validate → fast 400, before the lock

    def _do_write(cand: Candidate, sess: ResearchSession) -> str:
        try:
            writer = ZoteroWriter(settings().zotero_data_dir)
        except Exception as exc:  # noqa: BLE001 — Zotero-write is optional; surface as 503, don't 500.
            raise APIError(
                error="zotero_unavailable",
                message="Zotero is not available to write to",
                status_code=503,
            ) from exc
        new_key = _generate_zotero_key(set())
        try:
            writer.apply_feed_materialization(
                new_item_key=new_key,
                feed_payload=_feed_payload(cand),
                inbox_collection_name=collection_name,
                tags=[_TAG],
                note_title=f"Targeted Search: {sess.raw_query[:80]}",
                note_html=_note_html(cand, sess.raw_query),
            )
        except ZoteroWriteError as exc:
            # Locked DB after retries (Zotero open) — nothing written, no separate label
            # to keep. Surface, don't fake success (plan correction #4).
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
    if not added:
        return {"status": "already_added", "zotero_key": key}
    return {"status": "added", "zotero_key": key, "collection": collection_name}


__all__ = ["materialize_candidate"]
