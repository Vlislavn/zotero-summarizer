"""One generated-note stage/confirm boundary over the existing pending queue."""
from __future__ import annotations

import hashlib
import logging

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.zotero_actions import (
    DigestNotePayload, GeneratedNoteProposal, ZoteroTargetIdentity, preview_hash,
)
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.zotero._notes import DIGEST_NOTE_MARKER, build_digest_note_html
from zotero_summarizer.storage import pending_actions

LOGGER = logging.getLogger(__name__)


def _conflict(message: str) -> APIError:
    return APIError(error="generated_write_conflict", message=message, status_code=409)


def _proposal(item_key: str, entry: dict, revision: str,
              target: ZoteroTargetIdentity) -> GeneratedNoteProposal:
    target = ZoteroTargetIdentity.model_validate(target, strict=True)
    body = build_digest_note_html(PaperDigest.model_validate(entry["digest"]))
    return GeneratedNoteProposal(
        item_key=item_key, target=target, review_revision=revision,
        source_sha256=preview_hash(entry["review_identity"]),
        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        payload=DigestNotePayload(marker=DIGEST_NOTE_MARKER, note_html=body),
    )


def stage_current_digest(item_key: str, digest: PaperDigest | None = None) -> int | None:
    """Stage a published review against an existing writer; never construct one."""
    from zotero_summarizer.services.library.review_detail import classify_item_key, SOURCE_LIBRARY
    from zotero_summarizer.services.zotero.zotero import get_zotero_writer_or_raise

    if classify_item_key(item_key) != SOURCE_LIBRARY:
        return None
    with _review_cache.review_publication_lock():
        entry, revision = _review_cache.publication_snapshot(item_key)
        if not entry or not entry.get("digest") or not _review_cache._entry_condition(entry, None):
            return None
        if not revision or not entry.get("review_identity"):
            if digest is None:
                return None  # legacy copied reviews cannot supply proposal authority
            raise _conflict("Publish a fresh review before proposing a generated note")
        if digest is not None and preview_hash(digest.model_dump()) != preview_hash(entry["digest"]):
            raise _conflict("Digest differs from the published review")
        try:
            writer = get_zotero_writer_or_raise()
        except APIError as exc:
            if exc.error != "zotero_unavailable":
                raise
            LOGGER.warning("Generated note proposal unavailable item=%s diagnostic=zotero_unavailable",
                           item_key, extra={"diagnostic": "zotero_unavailable"})
            return None
        target = writer.target_identity()
        return pending_actions.stage(item_key, _proposal(item_key, entry, revision, target))


def validate_generated_changes(rows: list[dict], target: ZoteroTargetIdentity,
                               source_status: str) -> list[dict]:
    ready = []
    for row in rows:
        preview = pending_actions.preview(row)
        if preview is None:
            raise _conflict("Generated action lacks its immutable proposal")
        if preview["target"] != target.model_dump():
            raise _conflict("Generated preview is bound to a different native database or user library")
        if row["status"] == "applied":
            continue  # exact approval replay is an idempotent no-op
        if row["status"] != source_status:
            raise _conflict("Generated action is no longer actionable; uncertain delivery needs reconciliation")
        entry, revision = _review_cache.publication_snapshot(row["item_key"])
        if (not entry or not revision or not _review_cache.review_is_current(entry, row["item_key"]) or
                not _review_cache._entry_condition(entry, None) or
                _proposal(row["item_key"], entry, revision, target).preview_sha256 != preview["preview_sha256"]):
            raise _conflict("Generated preview is stale; inspect the new proposal instead")
        ready.append(row)
    return ready
