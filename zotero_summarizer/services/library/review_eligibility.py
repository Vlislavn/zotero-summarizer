"""A feed paper needs a usable, current full-text review before app-controlled Add."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage.feed_identity import LEGACY_FEED_PREFIX, is_stable_feed_key, row_feed_keys


@dataclass(frozen=True)
class ReviewedFeed:
    row_id: int
    stable_key: str


class ReviewRequired(APIError):
    def __init__(self) -> None:
        super().__init__(
            "review_required", "Generate a review before adding this paper to the library.", 409,
        )


def usable_review(entry: dict[str, Any] | None) -> bool:
    """A badge or placeholder is not enough; require actual paper-specific prose."""
    if not isinstance(entry, dict) or entry.get("needs_pdf") or entry.get("error"):
        return False
    digest = entry.get("digest")
    if not isinstance(digest, dict):
        return False
    if any(str(digest.get(field) or "").strip() for field in ("tldr", "executive_summary")):
        return True
    return any(str(finding).strip() for finding in (digest.get("key_findings") or []))


# Outdated reviews that are still a review of the SAME PDF bytes. ``_identity_drift``
# checks the PDF bytes first, so every drift reason except ``pdf_changed`` keeps
# ``source_sha256``; ``unverified``/``verification_outdated`` only arise when the
# whole stored identity still matches. ``pdf_changed`` (different bytes) and
# ``source_unresolved`` (current identity could not be computed) are deliberately
# absent, so a relaxed Add can never write a different paper to the owner's Zotero.
_SAME_PDF_OUTDATED = frozenset({
    "generation_updated", "unverified", "verification_outdated",
    "metadata_changed", "focus_changed", "source_changed",
})


def same_pdf_entry(entry: dict[str, Any] | None) -> bool:
    """A usable projected review that is current or outdated for a reason that keeps
    the PDF bytes (``pdf_changed``/``source_unresolved`` fail). Pure; Today reuses it."""
    if not usable_review(entry):
        return False
    freshness = entry.get("review_freshness") or {}
    return freshness.get("status") != "outdated" or freshness.get("reason") in _SAME_PDF_OUTDATED


def _same_pdf_review(key: str) -> bool:
    """A usable review that is still of this paper's PDF — current (unchanged fast
    path), or outdated for a reason that provably preserves the source bytes."""
    if usable_review(_review_cache.get_current_review(key)):
        return True
    entry = _review_cache.get_usable_review(key)
    return same_pdf_entry(entry) and (entry.get("review_freshness") or {}).get("status") == "outdated"


def review_ready(row: dict[str, Any]) -> bool:
    """A review is still required to unlock Add; a current OR outdated-same-PDF digest
    for this paper now counts (supersedes the earlier current-only gate). A missing,
    invalid, different-PDF (``pdf_changed``) or unresolved review still blocks."""
    return any(
        _same_pdf_review(key)
        for key in row_feed_keys(row) if is_stable_feed_key(key)
    )


def require_review(row: dict[str, Any]) -> ReviewedFeed:
    if not review_ready(row):
        raise ReviewRequired()
    stable = next((key for key in row_feed_keys(row) if is_stable_feed_key(key)), "")
    return ReviewedFeed(int(row.get("id") or 0), stable)


def require_authorization(row: dict[str, Any], proof: ReviewedFeed | None) -> None:
    """A checked Add may reuse its own proof; direct callers must check afresh."""
    if proof is None:
        require_review(row)
    elif (not isinstance(proof, ReviewedFeed) or proof.row_id != int(row["id"])
          or proof.stable_key not in row_feed_keys(row) or not is_stable_feed_key(proof.stable_key)):
        raise ReviewRequired()


def require_feed_review(db_path: Path, item_key: str) -> None:
    """Guard positive feed verdicts before their label transaction (online or sync)."""
    with feeds_storage.open_triage_conn(db_path) as conn:
        row = feeds_storage.get_processed_feed_item_by_stable_key(conn, item_key)
        if row is None and item_key.startswith(LEGACY_FEED_PREFIX):
            suffix = item_key[len(LEGACY_FEED_PREFIX):]
            if suffix.isdigit():
                row = feeds_storage.get_processed_feed_item_by_id(conn, int(suffix))
    if row is None:
        raise ReviewRequired()
    paper = dict(row)
    if not paper.get("materialized_zotero_key"):
        require_review(paper)
