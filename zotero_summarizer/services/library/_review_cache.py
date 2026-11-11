"""Deep-review JSON cache primitives (split from ``deep_review`` for the LOC cap).

The store maps a key → review entry in ``deep_reviews.json``. The key is a Zotero
``item_key`` for a library item, or a ``stable_feed_key`` for an IN-PLACE Today review
(no Zotero write). Quality is gate-independent, so unlike ``reading_queue`` this is NOT
keyed by the gate sha; re-run to refresh. ``deep_review`` re-exports these so
``deep_review._read_all`` / ``get_cached_review`` / ``copy_review`` stay the public seam.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from filelock import FileLock
from typing import Any
from uuid import uuid4

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.runtime import memoized, request_memo
from zotero_summarizer.services._common import LOGGER, now_iso_z, settings, write_json_atomic

_CACHE_FILENAME = "deep_reviews.json"
_CACHE_LOCK = threading.RLock()   # guards cache reads, quarantine, and read-merge-write
REVIEW_CONTRACT_VERSION = 3


def _cache_path():
    return settings().model_dir / _CACHE_FILENAME


def _read_all(*, readonly: bool = False) -> dict[str, Any]:
    path = _cache_path()
    with _CACHE_LOCK:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            if readonly:
                raise ValueError("Invalid deep-review cache bytes") from exc
            return _recover_corrupt_cache(exc)
        reviews = payload.get("reviews") if isinstance(payload, dict) else None
        if not isinstance(reviews, dict):
            if readonly:
                raise ValueError("Invalid deep-review cache envelope")
            return _recover_corrupt_cache(ValueError("Invalid deep-review cache envelope"))
        return reviews


def _quarantine_corrupt_cache(path) -> Any:
    """Move invalid bytes aside without overwriting an earlier recovery copy."""
    suffix = 0
    while True:
        tail = ".corrupt" if suffix == 0 else f".corrupt-{suffix}"
        backup = path.with_name(path.name + tail)
        if not backup.exists():
            path.replace(backup)
            return backup
        suffix += 1


def _write_all(reviews: dict[str, Any], *, publication_key: str | None = None) -> None:
    with review_publication_lock():
        old = _read_all()
        envelope = (json.loads(_cache_path().read_text(encoding="utf-8"))
                    if _cache_path().exists() else {})
        prior_revisions = envelope.get("review_revisions", {})
        revisions = {}
        for key, entry in reviews.items():
            revision = prior_revisions.get(key)
            revisions[key] = (str(uuid4()) if key == publication_key or
                              entry != old.get(key) or not revision else revision)
        write_json_atomic(_cache_path(), {
            "updated_at": now_iso_z(), "reviews": reviews, "review_revisions": revisions,
        })


def _write_one(item_key: str, entry: dict[str, Any]) -> None:
    """Publish a fresh revision even for A→B→A, without changing review DTOs."""
    with review_publication_lock():
        reviews = _read_all()
        reviews[item_key] = entry
        _write_all(reviews, publication_key=item_key)


def _depth(entry: dict[str, Any] | None) -> str:
    return str(((entry or {}).get("provenance") or {}).get("review_depth") or "thorough")


def keep_previous_if_downgraded(entry: dict[str, Any], prior: dict[str, Any] | None) -> None:
    """A fast re-run must not silently destroy a deeper review: keep the prior usable
    entry as ``entry["previous_review"]`` (restore: copy it back over the key in
    ``deep_reviews.json``). Only the latest prior is kept; projections never expose it."""
    from zotero_summarizer.services.library.review_eligibility import usable_review  # lazy: it imports this module

    if usable_review(prior) and _depth(entry) == "fast" and _depth(prior) != "fast":
        entry["previous_review"] = {key: value for key, value in prior.items() if key != "previous_review"}


def get_cached_review(item_key: str) -> dict[str, Any] | None:
    """Project stored review through current reading policy; never rewrite the cache."""
    if not item_key:
        return None
    entry = _read_all().get(item_key)
    if entry and not _entry_condition(entry, None):
        return None
    from zotero_summarizer.services.library.review_fleet.propose import project_review
    return project_review(entry) if entry else entry


def export_review_audit(item_key: str) -> dict[str, Any]:
    """Exact-key, read-only export: no policy projection, acquisition or cache repair."""
    import re
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._digest_audit_export import retained_audit_status

    if not re.fullmatch(r'(?:[A-Za-z0-9]{8}|feed:[gdal]:[a-f0-9]{64})', item_key):
        raise APIError('validation_error', 'Expected an exact Zotero or stable feed key', 422)
    try:
        entry = _read_all(readonly=True).get(item_key)
        if entry is None:
            raise APIError('not_found', 'No cached review for this item', 404)
        if not isinstance(entry, dict):
            raise ValueError('Invalid review entry')
        if not _entry_condition(entry, None):
            return {'item_key': item_key, 'digest': None, 'audit_status': 'unavailable',
                    'reason': 'review_condition_mismatch', 'verification_audit': None, 'retained_evidence': None}
        if entry.get('digest') is not None:
            PaperDigest.model_validate(entry['digest'])
        status, reason = retained_audit_status(entry)
    except ValueError as exc:
        raise APIError('audit_invalid', 'Cached verification evidence is malformed', 422) from exc
    retained = None
    if status == 'available':
        retained = {'scope': 'retained_source_only', 'current_item_binding': 'not_established',
                    'verification_audit': entry['verification_audit']}
        if entry['review_identity']['source_kind'] != 'override':
            status, reason = 'unavailable', 'current_item_binding_unavailable'
    return {'item_key': item_key, 'digest': entry.get('digest'), 'audit_status': status, 'reason': reason,
            'verification_audit': entry.get('verification_audit') if status == 'available' else None,
            'retained_evidence': retained,
            'older_rules_evidence': _older_rules_evidence(entry) if reason == 'audit_contract_changed' else None}


def _older_rules_evidence(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Quotes of an audit whose only drift is the check rules (paper bytes unchanged)."""
    from zotero_summarizer.services.library._digest_audit_export import retained_audit_status
    try:
        status, _ = retained_audit_status(entry, older_rules_ok=True)
    except (APIError, ValueError):
        return None
    return {'scope': 'retained_source_only', 'verification_audit': entry['verification_audit']} if status == 'available' else None


def _structural_reason(entry: dict[str, Any] | None, item_key: str) -> str | None:
    """``None`` when the entry is a complete, usable review; else why it is unusable
    (incomplete or corrupt — hidden, as distinct from merely outdated)."""
    if not entry or not _entry_condition(entry, None):
        return "no_review"
    if entry.get("review_contract_version") != REVIEW_CONTRACT_VERSION:
        return "contract_version"
    if entry.get("digest") is None and not entry.get("needs_pdf"):
        return "no_digest"
    if not item_key or not isinstance(entry.get("review_identity"), dict):
        return "no_identity"
    return None


def _identity_drift(stored: dict[str, Any], current: dict[str, Any]) -> str:
    """Human-readable reason a valid review is outdated (its first changed input)."""
    if stored.get("source_sha256") != current.get("source_sha256"):
        return "pdf_changed"
    if stored.get("source_kind") != current.get("source_kind"):
        return "source_changed"
    if stored.get("focus_prompt") != current.get("focus_prompt"):
        return "focus_changed"
    if stored.get("generation_sha256") != current.get("generation_sha256"):
        return "generation_updated"
    return "metadata_changed"


def review_status(entry: dict[str, Any] | None, item_key: str = "") -> tuple[str, str]:
    """``('current'|'outdated'|'invalid', reason)``. An ``outdated`` review is still
    valid and is SHOWN with a badge + refresh; only ``invalid`` (incomplete/corrupt)
    is hidden. ``current`` preserves the exact prior ``review_is_current`` semantics."""
    reason = _structural_reason(entry, item_key)
    if reason is not None:
        return "invalid", reason
    stored = entry["review_identity"]
    try:
        from zotero_summarizer.services.library._review_identity import current_review_identity
        from zotero_summarizer.services.library._digest_audit_export import retained_audit_status
        current = current_review_identity(item_key, stored)
        if current != stored:
            return "outdated", _identity_drift(stored, current)
        audit = entry.get("verification_audit")
        has_digest = entry.get("digest") is not None or entry.get("model_digest")
        if has_digest and audit is None:
            # Legacy review written before the verification audit existed: show it
            # badged (never silently hidden), but it is not 'current' for sync/prewarm.
            return "outdated", "unverified"
        if audit is not None and retained_audit_status(entry, current_identity=current)[0] != "available":
            return "outdated", "verification_outdated"
        return "current", ""
    except (APIError, AttributeError, OSError, RuntimeError, ValueError):
        return "outdated", "source_unresolved"


def review_is_current(entry: dict[str, Any] | None, item_key: str = "") -> bool:
    return review_status(entry, item_key)[0] == "current"


def _project_usable(entry: dict[str, Any] | None, item_key: str) -> dict[str, Any] | None:
    """Project a current OR outdated (valid) review, tagged with its freshness so the
    UI/Today can badge an outdated one; ``invalid``/missing returns ``None`` (hidden)."""
    status, reason = review_status(entry, item_key)
    if status == "invalid":
        return None
    from zotero_summarizer.services.library.review_fleet.propose import project_review
    return {**project_review(entry), "review_freshness": {"status": status, "reason": reason}}


def get_usable_review(item_key: str) -> dict[str, Any] | None:
    if not item_key:
        return None
    return _project_usable(_read_all().get(item_key), item_key)


def usable_reviews() -> dict[str, Any]:
    """Current + outdated (valid) reviews, each tagged with ``review_freshness``.
    Display/Today seam: the strict ``current_reviews`` stays for correctness-critical
    consumers (Zotero note sync, regeneration prewarm)."""
    from zotero_summarizer.services.library._review_identity import prime_pdf_paths

    def compute() -> dict[str, Any]:
        entries = _read_all()
        prime_pdf_paths(entries)
        return {key: projected for key, entry in entries.items()
                if (projected := _project_usable(entry, key)) is not None}

    with request_memo():
        return memoized("usable_reviews", compute)


def get_current_review(item_key: str) -> dict[str, Any] | None:
    if not item_key:
        return None
    return _project_current(_read_all().get(item_key), item_key)


def cached_review_keys() -> set[str]:
    """All raw stored keys, not currentness or permission to skip ordinary review."""
    return set(_read_all())


def current_review_keys() -> set[str]:
    return {key for key, entry in _read_all().items() if review_is_current(entry, key)}


def _project_current(entry: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if not review_is_current(entry, key):
        return None
    from zotero_summarizer.services.library.review_fleet.propose import project_review
    return project_review(entry)


def current_reviews() -> dict[str, Any]:
    """Current projected reviews. Within a ``request_memo`` scope computed once (read-only result)."""
    from zotero_summarizer.services.library._review_identity import prime_pdf_paths

    def compute() -> dict[str, Any]:
        entries = _read_all()
        prime_pdf_paths(entries)
        return {key: projected for key, entry in entries.items()
                if (projected := _project_current(entry, key)) is not None}

    with request_memo():  # one pass hashes each PDF / resolves each library item at most once
        return memoized("current_reviews", compute)


def current_reviews_for_keys(item_keys: list[str]) -> dict[str, Any]:
    """Check only requested identities; invalid cache bytes fail without repair."""
    keys = {key for key in item_keys if key}
    if not keys:
        return {}
    stored = _read_all(readonly=True)
    return {key: projected for key in keys
            if (projected := _project_current(stored.get(key), key)) is not None}


def usable_reviews_for_keys(item_keys: list[str]) -> dict[str, Any]:
    """Display/Today seam for a bounded key set: current OR outdated-but-valid
    reviews, each tagged with ``review_freshness`` (mirrors ``usable_reviews`` the
    way ``current_reviews_for_keys`` mirrors ``current_reviews``)."""
    keys = {key for key in item_keys if key}
    if not keys:
        return {}
    stored = _read_all(readonly=True)
    return {key: projected for key in keys
            if (projected := _project_usable(stored.get(key), key)) is not None}


def copy_review(src_key: str, dst_key: str) -> bool:
    """Copy a cached review from ``src_key`` to ``dst_key`` (no-op if absent / same key).
    Lets an in-place Today review (cached under ``stable_feed_key``) persist onto the new
    library item_key when the paper is materialized into Zotero."""
    src, dst = (src_key or "").strip(), (dst_key or "").strip()
    if not src or not dst or src == dst:
        return False
    entry = _read_all().get(src)
    if entry is None:
        return False
    _write_one(dst, entry)
    return True


__all__ = [
    "_cache_path", "_read_all", "_write_all", "_write_one",
    "REVIEW_CONTRACT_VERSION", "get_cached_review", "get_current_review",
    "review_is_current", "review_status", "get_usable_review", "usable_reviews",
    "usable_reviews_for_keys",
    "cached_review_keys", "current_review_keys", "current_reviews", "copy_review",
]


_PUBLICATION_LOCKS: dict[str, FileLock] = {}


@contextmanager
def review_publication_lock(*, require_publication: bool = True):
    """Process coordination, plus publication-file locking when source requires it."""
    with _CACHE_LOCK:
        if not require_publication:
            yield
            return
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        name = str(path) + ".lock"
        lock = _PUBLICATION_LOCKS.setdefault(name, FileLock(name))
        with lock:
            yield


def _recover_corrupt_cache(error: Exception) -> dict[str, Any]:
    with review_publication_lock():
        try:
            return _read_all(readonly=True)  # another process may have repaired it
        except (ValueError, UnicodeDecodeError):
            backup = _quarantine_corrupt_cache(_cache_path())
            LOGGER.warning("quarantined invalid deep-review cache to %s (%s)", backup, error)
            return {}


def publication_snapshot(item_key: str) -> tuple[dict[str, Any] | None, str | None]:
    """Exact stored entry + publication UUID; callers hold the publication lock."""
    if not _cache_path().exists():
        return None, None
    payload = json.loads(_cache_path().read_text(encoding="utf-8"))
    reviews = payload["reviews"]
    return reviews.get(item_key), payload.get("review_revisions", {}).get(item_key)


def _entry_condition(entry: dict[str, Any], interpretation_schema: str | None) -> bool:
    from zotero_summarizer.services.library._review_identity import _condition_matches

    stored = entry.get("review_identity") or {}
    if not isinstance(stored, dict) or not _condition_matches(stored, interpretation_schema):
        return False
    if interpretation_schema is None:
        audit = entry.get("verification_audit")
        return not (any(key in entry for key in ("interpretation", "interpretation_qualification", "scientific_call")) or
                    any(isinstance(entry.get(key), dict) and "interpretation" in entry[key]
                        for key in ("digest", "model_digest")) or
                    isinstance(audit, dict) and any(key in audit for key in (
                        "interpretation_evidence", "verified_interpretation", "interpretation_bindings")))
    return True
