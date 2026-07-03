"""App-owned RSS reader backed by ``rss_feeds`` / ``rss_items``."""
from __future__ import annotations

import ipaddress
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

import httpx

from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import rss as rss_storage
from zotero_summarizer.storage.feed_identity import (
    doi_from_text,
    stable_feed_key_from_item,
)


class RssUrlRejected(ValueError):
    """Raised when a feed URL is not safe for server-side fetching."""


def _reject_private_ip(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise RssUrlRejected(f"RSS URL resolves to a non-public address: {host}")


def validate_rss_url(url: str) -> str:
    """Validate a user-supplied RSS URL before network fetch.

    Only public ``http``/``https`` network URLs are allowed. Local files,
    loopback, link-local, private, reserved, and unresolved hosts are rejected.
    """
    safe_url = str(url or "").strip()
    parts = urlsplit(safe_url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise RssUrlRejected("RSS URL must use http or https")
    if not parts.hostname:
        raise RssUrlRejected("RSS URL must include a hostname")
    host = parts.hostname
    _reject_private_ip(host)
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RssUrlRejected(f"RSS hostname could not be resolved: {host}") from exc
    if not infos:
        raise RssUrlRejected(f"RSS hostname could not be resolved: {host}")
    for info in infos:
        address = str(info[4][0])
        _reject_private_ip(address)
    return safe_url


def _fetch_public_url(url: str, *, timeout: float) -> tuple[str, httpx.Headers]:
    current = validate_rss_url(url)
    for _ in range(5):
        with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=False) as client:
            resp = client.get(
                current,
                headers={
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
                    "User-Agent": "zotero-summarizer/0.1 RSS reader",
                },
            )
        if 300 <= resp.status_code < 400 and resp.headers.get("location"):
            current = validate_rss_url(urljoin(current, str(resp.headers["location"])))
            continue
        resp.raise_for_status()
        return resp.text, resp.headers
    raise RssUrlRejected("RSS URL redirected too many times")


def _entry_authors(entry: Any) -> str:
    authors = entry.get("authors") or []
    names: list[str] = []
    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, dict):
                name = str(author.get("name") or "").strip()
            else:
                name = str(author or "").strip()
            if name:
                names.append(name)
    author = str(entry.get("author") or "").strip()
    if author and not names:
        names.append(author)
    return "; ".join(names)


def _entry_to_item(entry: Any, *, feed: dict[str, Any], feed_title: str) -> dict[str, Any]:
    link = str(entry.get("link") or "").strip()
    entry_id = str(entry.get("id") or "").strip()
    guid = str(entry.get("guid") or entry_id or "").strip()
    summary = str(entry.get("summary") or entry.get("description") or "").strip()
    doi = str(entry.get("doi") or "").strip() or doi_from_text(entry_id, link, summary)
    arxiv_id = _arxiv_id_from_url_or_doi(link or entry_id, doi)
    item = {
        "feed_library_id": int(feed["id"]),
        "item_id": 0,
        "rss_feed_id": int(feed["id"]),
        "source": "app_rss",
        "source_type": "app_rss",
        "feed_name": str(feed.get("name") or ""),
        "guid": guid,
        "entry_id": entry_id,
        "title": str(entry.get("title") or "").strip() or "Untitled",
        "abstract": summary,
        "url": link,
        "canonical_url": link,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "publication_date": str(entry.get("published") or entry.get("updated") or "").strip(),
        "publication_title": str(feed_title or feed.get("name") or "").strip(),
        "authors": _entry_authors(entry),
        "item_type": "journalArticle",
    }
    item["stable_feed_key"] = stable_feed_key_from_item(item)
    return item


class AppRssReader:
    """RSS reader that treats the app DB as the subscription/source of truth."""

    def __init__(self, triage_db_path: Path):
        self.triage_db_path = Path(triage_db_path)

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        import sqlite3

        self.triage_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.triage_db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            feeds_storage.init_feeds_schema(conn)
            yield conn
        finally:
            conn.close()

    def refresh_feeds(
        self,
        *,
        max_feeds: int = 10,
        max_new_items_per_feed: int = 25,
        per_feed_timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Fetch a bounded pass of enabled feeds and store parsed items.

        This does not score anything and does not call an LLM.
        """
        import feedparser

        fetched = 0
        inserted = 0
        updated = 0
        errors: list[dict[str, str]] = []
        with self._conn() as conn:
            feeds = rss_storage.list_rss_feeds(conn)[: max(0, int(max_feeds))]
            for feed in feeds:
                fetched += 1
                try:
                    body, headers = _fetch_public_url(str(feed["url"]), timeout=float(per_feed_timeout))
                    parsed = feedparser.parse(body)
                    feed_title = str((parsed.feed or {}).get("title") or feed.get("name") or "")
                    for entry in list(parsed.entries or [])[: max(0, int(max_new_items_per_feed))]:
                        item = _entry_to_item(entry, feed=feed, feed_title=feed_title)
                        if not item.get("stable_feed_key"):
                            continue
                        item_id, was_inserted = rss_storage.upsert_rss_item(
                            conn, rss_feed_id=int(feed["id"]), item=item,
                        )
                        conn.execute(
                            "UPDATE rss_items SET rss_feed_id = ? WHERE id = ?",
                            (int(feed["id"]), item_id),
                        )
                        inserted += 1 if was_inserted else 0
                        updated += 0 if was_inserted else 1
                    rss_storage.record_rss_fetch_result(
                        conn,
                        int(feed["id"]),
                        error=None,
                        etag=headers.get("etag"),
                        modified=headers.get("last-modified"),
                    )
                    conn.commit()
                except Exception as exc:
                    rss_storage.record_rss_fetch_result(
                        conn, int(feed["id"]), error=f"{type(exc).__name__}: {exc}",
                    )
                    conn.commit()
                    errors.append({"feed": str(feed.get("name") or feed.get("id")), "error": str(exc)})
        return {"feeds": fetched, "inserted": inserted, "updated": updated, "errors": errors}

    def get_feed_groups(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            feeds = rss_storage.list_rss_feeds(conn, include_disabled=True)
        return [
            {
                "library_id": int(feed["id"]),
                "name": str(feed["name"] or ""),
                "url": str(feed["url"] or ""),
                "last_update": str(feed["last_fetched_at"] or ""),
                "last_check": str(feed["last_fetched_at"] or ""),
                "last_check_error": str(feed["last_error"] or "") or None,
                "refresh_interval_minutes": 0,
                "enabled": bool(feed["enabled"]),
                "source": str(feed["source"] or "app"),
            }
            for feed in feeds
        ]

    def get_feed_items(
        self,
        feed_library_id: int | None = None,
        since: str | None = None,
        limit: int = 1000,
        unread_only: bool = False,
        order: str = "newest_first",
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = rss_storage.list_rss_items(
                conn,
                rss_feed_id=feed_library_id,
                unread_only=unread_only,
                limit=limit,
                order=order,
            )
        out: list[dict[str, Any]] = []
        for row in rows:
            if since and str(row.get("created_at") or "") < since:
                continue
            out.append(
                {
                    "item_id": int(row["id"]),
                    "feed_library_id": int(row["rss_feed_id"]),
                    "rss_feed_id": int(row["rss_feed_id"]),
                    "source": "app_rss",
                    "source_type": "app_rss",
                    "stable_feed_key": str(row["stable_feed_key"] or ""),
                    "feed_name": str(row["feed_name"] or ""),
                    "guid": str(row["guid"] or row["entry_id"] or row["stable_feed_key"] or ""),
                    "title": str(row["title"] or ""),
                    "abstract": str(row["abstract"] or ""),
                    "url": str(row["url"] or row["canonical_url"] or ""),
                    "doi": str(row["doi"] or ""),
                    "arxiv_id": str(row["arxiv_id"] or ""),
                    "publication_date": str(row["publication_date"] or ""),
                    "publication_title": str(row["publication_title"] or ""),
                    "authors": str(row["authors"] or ""),
                    "item_type": str(row["item_type"] or "journalArticle"),
                    "read_time": str(row["read_at"] or "") or None,
                    "date_added": str(row["created_at"] or ""),
                    "date_modified": str(row["updated_at"] or ""),
                }
            )
        return out

    def find_by_external_id(self, *, doi: str | None = None, arxiv_id: str | None = None) -> dict[str, Any] | None:
        """Optional Zotero-library duplicate hook.

        The app-owned reader can run with no Zotero DB. Returning ``None`` keeps
        Zotero as an optional adapter; DOI/arXiv processed-row dedup still runs.
        """
        return None


__all__ = ["AppRssReader", "RssUrlRejected", "validate_rss_url"]
