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


def get_cached_review(item_key: str) -> dict[str, Any] | None:
    """Project stored review through current reading policy; never rewrite the cache."""
    if not item_key:
        return None
    entry = _read_all().get(item_key)
    if not entry or not entry.get("digest"):
        return entry
    from zotero_summarizer.services.library.review_fleet.propose import apply_reading_policy
    digest, raw, flags = apply_reading_policy(entry["digest"], entry.get("quality"), entry.get("goal_summaries"))
    if digest == entry["digest"] and not flags:
        return entry
    return {**entry, "digest": digest, "model_read_decision": entry.get("model_read_decision", raw),
            "reading_policy_flags": list(dict.fromkeys([*entry.get("reading_policy_flags", []), *flags]))}


def export_review_audit(item_key: str) -> dict[str, Any]:
    """Exact-key, read-only export: no policy projection, acquisition or cache repair."""
    import re
    from zotero_summarizer.services.library._digest_audit_export import retained_audit_status

    if not re.fullmatch(r'(?:[A-Za-z0-9]{8}|feed:[gdal]:[a-f0-9]{64})', item_key):
        raise APIError('validation_error', 'Expected an exact Zotero or stable feed key', 422)
    try:
        entry = _read_all(readonly=True).get(item_key)
        if entry is None:
            raise APIError('not_found', 'No cached review for this item', 404)
        if not isinstance(entry, dict):
            raise ValueError('Invalid review entry')
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
            'retained_evidence': retained}


def review_is_current(entry: dict[str, Any] | None, item_key: str = "") -> bool:
    if not entry or entry.get("review_contract_version") != REVIEW_CONTRACT_VERSION:
        return False
    if entry.get("digest") is None and not entry.get("needs_pdf"):
        return False
    stored = entry.get("review_identity")
    if not item_key or not isinstance(stored, dict):
        return False
    try:
        from zotero_summarizer.services.library._review_identity import current_review_identity
        from zotero_summarizer.services.library._digest_audit_export import retained_audit_status
        if entry.get('verification_audit') is not None and retained_audit_status(entry)[0] != 'available':
            return False
        return current_review_identity(item_key, stored) == stored
    except (APIError, AttributeError, OSError, RuntimeError, ValueError):
        return False


def get_current_review(item_key: str) -> dict[str, Any] | None:
    entry = get_cached_review(item_key)
    return entry if review_is_current(entry, item_key) else None


def cached_review_keys() -> set[str]:
    """All item_keys with a stored deep review — one cache read (prewarm reuses this
    instead of calling ``get_cached_review`` per row, which re-reads the whole file)."""
    return set(_read_all())


def current_review_keys() -> set[str]:
    return {key for key, entry in _read_all().items() if review_is_current(entry, key)}


def current_reviews() -> dict[str, Any]:
    return {key: entry for key, entry in _read_all().items() if review_is_current(entry, key)}


def copy_review(src_key: str, dst_key: str) -> bool:
    """Copy a cached review from ``src_key`` to ``dst_key`` (no-op if absent / same key).
    Lets an in-place Today review (cached under ``stable_feed_key``) persist onto the new
    library item_key when the paper is materialized into Zotero."""
    src, dst = (src_key or "").strip(), (dst_key or "").strip()
    if not src or not dst or src == dst:
        return False
    entry = get_cached_review(src)
    if entry is None:
        return False
    _write_one(dst, entry)
    return True


__all__ = [
    "_cache_path", "_read_all", "_write_all", "_write_one",
    "REVIEW_CONTRACT_VERSION", "get_cached_review", "get_current_review",
    "review_is_current", "cached_review_keys", "current_review_keys", "current_reviews", "copy_review",
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
        return not ("interpretation" in entry or "interpretation_qualification" in entry or
                    isinstance(audit, dict) and "interpretation_evidence" in audit)
    return True
