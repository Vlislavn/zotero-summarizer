"""Stable identity for RSS/feed-sourced papers.

The app used to key feed labels as ``feed:<feed_item_id>``, where
``feed_item_id`` was Zotero's local row id. That id is not stable across RSS
sources, re-imports, or an app-owned reader. This module is the single
constructor for the durable feed identity used by new writes.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi


STABLE_FEED_PREFIX = "feed:"
LEGACY_FEED_PREFIX = "feed:"

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(namespace: str, value: str) -> str:
    return f"feed:{namespace}:{_hash(value)}"


def _trim(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_url(value: Any) -> str:
    raw = _trim(value)
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return raw.rstrip("/")
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_guid(value: Any) -> str:
    """Normalize an RSS GUID/entry id without treating local DB ids as GUIDs."""
    text = _trim(value)
    if not text:
        return ""
    if text.startswith(("http://", "https://", "HTTP://", "HTTPS://")):
        return _normalize_url(text)
    return text.casefold()


def normalize_canonical_link(value: Any) -> str:
    return _normalize_url(value)


def doi_from_text(*values: Any) -> str:
    for value in values:
        text = _trim(value)
        if not text:
            continue
        direct = normalize_doi(text)
        if direct.startswith("10."):
            return direct
        match = _DOI_RE.search(text)
        if match:
            return normalize_doi(match.group(0).rstrip(".,;)"))
    return ""


def stable_feed_key_from_parts(
    *,
    guid: Any = None,
    entry_id: Any = None,
    doi: Any = None,
    arxiv_id: Any = None,
    canonical_link: Any = None,
    url: Any = None,
    link: Any = None,
) -> str:
    """Return the durable feed key for an RSS item, or ``""`` if unkeyable.

    Precedence follows the cutover plan:
      1. normalized RSS GUID / entry id
      2. normalized DOI
      3. normalized arXiv id
      4. normalized canonical link
    """
    guid_norm = normalize_guid(guid) or normalize_guid(entry_id)
    if guid_norm:
        return _key("g", guid_norm)

    doi_norm = normalize_doi(doi or "")
    if not doi_norm:
        doi_norm = doi_from_text(canonical_link, url, link)
    if doi_norm:
        return _key("d", doi_norm)

    arxiv_norm = normalize_arxiv_id(arxiv_id or "")
    if not arxiv_norm:
        link_candidate = canonical_link or url or link or ""
        if "arxiv" in str(link_candidate).casefold():
            arxiv_norm = normalize_arxiv_id(link_candidate)
    if arxiv_norm:
        return _key("a", arxiv_norm)

    link_norm = normalize_canonical_link(canonical_link or url or link)
    if link_norm:
        return _key("l", link_norm)
    return ""


def stable_feed_key_from_item(item: dict[str, Any]) -> str:
    """Build a stable feed key from the common feed item dict shape."""
    return stable_feed_key_from_parts(
        guid=item.get("guid"),
        entry_id=item.get("entry_id") or item.get("rss_entry_id"),
        doi=item.get("doi"),
        arxiv_id=item.get("arxiv_id"),
        canonical_link=item.get("canonical_url"),
        url=item.get("url"),
        link=item.get("link"),
    )


def legacy_feed_key(feed_item_id: Any) -> str:
    fid = int(feed_item_id or 0)
    return f"feed:{fid}" if fid > 0 else ""


def is_legacy_feed_key(item_key: str) -> bool:
    suffix = str(item_key or "").removeprefix(LEGACY_FEED_PREFIX)
    return bool(suffix) and suffix.isdigit()


def is_stable_feed_key(item_key: str) -> bool:
    text = str(item_key or "").strip()
    if not text.startswith(STABLE_FEED_PREFIX) or is_legacy_feed_key(text):
        return False
    parts = text.split(":")
    return len(parts) == 3 and parts[1] in {"g", "d", "a", "l"} and len(parts[2]) == 64


def row_feed_keys(row: dict[str, Any]) -> list[str]:
    """Stable key first, then legacy fallback keys for compatibility lookups."""
    keys: list[str] = []
    stable = str(row.get("stable_feed_key") or "").strip()
    if stable:
        keys.append(stable)
    else:
        computed = stable_feed_key_from_item(row)
        if computed:
            keys.append(computed)
    legacy = legacy_feed_key(row.get("feed_item_id"))
    if legacy and legacy not in keys:
        keys.append(legacy)
    processed = f"processed:{row.get('id')}" if row.get("id") is not None else ""
    if processed and processed not in keys:
        keys.append(processed)
    return keys


__all__ = [
    "doi_from_text",
    "is_legacy_feed_key",
    "is_stable_feed_key",
    "legacy_feed_key",
    "normalize_canonical_link",
    "normalize_guid",
    "row_feed_keys",
    "stable_feed_key_from_item",
    "stable_feed_key_from_parts",
]
