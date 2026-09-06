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
from typing import Any

from zotero_summarizer.services._common import now_iso_z, settings, write_json_atomic

_CACHE_FILENAME = "deep_reviews.json"
_CACHE_LOCK = threading.Lock()    # guards the read-merge-write of deep_reviews.json
REVIEW_CONTRACT_VERSION = 3


def _cache_path():
    return settings().model_dir / _CACHE_FILENAME


def _read_all() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("reviews") or {}


def _write_all(reviews: dict[str, Any]) -> None:
    write_json_atomic(_cache_path(), {"updated_at": now_iso_z(), "reviews": reviews})


def _write_one(item_key: str, entry: dict[str, Any]) -> None:
    """Persist ONE review entry via a locked read-merge-write, so concurrent per-item
    workers never clobber each other's keys in the shared ``deep_reviews.json``."""
    with _CACHE_LOCK:
        reviews = _read_all()
        reviews[item_key] = entry
        _write_all(reviews)


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


def review_is_current(entry: dict[str, Any] | None, item_key: str = "") -> bool:
    if not entry or entry.get("review_contract_version") != REVIEW_CONTRACT_VERSION:
        return False
    stored = entry.get("review_identity")
    if not item_key or not isinstance(stored, dict):
        return False
    try:
        from zotero_summarizer.services.library._review_identity import current_review_identity
        return current_review_identity(item_key, stored) == stored
    except (AttributeError, OSError, RuntimeError, ValueError):
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
    "review_is_current", "cached_review_keys", "current_review_keys", "copy_review",
]
