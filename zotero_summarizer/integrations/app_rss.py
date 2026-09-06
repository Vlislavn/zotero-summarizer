"""App-owned RSS reader backed by ``rss_feeds`` / ``rss_items``."""

from __future__ import annotations

import ipaddress
import socket
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from zotero_summarizer.integrations._zotero_read_common import _INJECTION_CHAR_PATTERN, _arxiv_id_from_url_or_doi
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import rss as rss_storage
from zotero_summarizer.storage.feed_identity import (
    doi_from_text,
    stable_feed_key_from_item,
)
from zotero_summarizer.settings import offline_requested


class RssUrlRejected(ValueError):
    """A rejected feed destination or response document."""


def _reject_private_ip(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip.is_global or ip.is_multicast:
        raise RssUrlRejected(f"RSS URL resolves to a non-public address: {host}")


def _resolve_public_url(url: str) -> tuple[httpx.URL, str]:
    try:
        target = httpx.URL(str(url).strip())
    except httpx.InvalidURL as exc:
        raise RssUrlRejected("Malformed RSS URL") from exc
    if target.scheme not in {"http", "https"}:
        raise RssUrlRejected("RSS URL must use http or https")
    if not target.host:
        raise RssUrlRejected("RSS URL must include a hostname")
    if target.userinfo:
        raise RssUrlRejected("RSS URL must not include credentials")
    if target.port is not None and not 1 <= target.port <= 65535:
        raise RssUrlRejected("RSS URL port must be between 1 and 65535")
    host = target.raw_host.decode("ascii")
    _reject_private_ip(host)
    try:
        infos = socket.getaddrinfo(
            host,
            target.port or (443 if target.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RssUrlRejected(f"RSS hostname could not be resolved: {host}") from exc
    if not infos:
        raise RssUrlRejected(f"RSS hostname could not be resolved: {host}")
    for info in infos:
        address = str(info[4][0])
        _reject_private_ip(address)
    return target, str(infos[0][4][0])


def validate_rss_url(url: str) -> str:
    """Validate and normalize a public HTTP(S) destination."""
    target, _ = _resolve_public_url(url)
    return str(target)


def stream_public_url(client: httpx.Client, url: str):
    """Pin the request to a validated IP, retaining its origin Host and TLS name.

    HTTPX's sni_hostname preserves certificate verification without a second
    hostname lookup. Redirects must re-enter this boundary.
    """
    target, address = _resolve_public_url(url)
    return client.stream(
        "GET", target.copy_with(host=address), follow_redirects=False,
        headers={"Host": target.netloc.decode("ascii")},
        extensions={"sni_hostname": target.raw_host.decode("ascii")},
    )


_RSS_MAX_BYTES = 8 * 1024 * 1024


def _read_rss_body(resp: httpx.Response) -> bytes:
    encoding = resp.headers.get("content-encoding", "identity").lower()
    if encoding not in {"identity", "gzip"}:
        raise RssUrlRejected("Unsupported RSS content encoding")
    try:
        length = int(resp.headers.get("content-length", "0"))
    except ValueError as exc:
        raise RssUrlRejected("Invalid RSS response size") from exc
    if not 0 <= length <= _RSS_MAX_BYTES:
        raise RssUrlRejected("RSS response exceeds size limit")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    body = bytearray()
    wire_bytes = 0
    for chunk in resp.iter_raw():
        wire_bytes += len(chunk)
        if wire_bytes > _RSS_MAX_BYTES:
            raise RssUrlRejected("RSS response exceeds size limit")
        if decoder is not None:
            try:
                chunk = decoder.decompress(chunk, _RSS_MAX_BYTES - len(body) + 1)
            except zlib.error as exc:
                raise RssUrlRejected("Invalid RSS gzip encoding") from exc
        if len(body) + len(chunk) > _RSS_MAX_BYTES:
            raise RssUrlRejected("RSS response exceeds size limit")
        body.extend(chunk)
    if decoder is not None and (not decoder.eof or decoder.unused_data):
        raise RssUrlRejected("Invalid or trailing RSS gzip encoding")
    return bytes(body)


def _fetch_public_url(url: str, *, timeout: float) -> tuple[bytes, httpx.Headers]:
    current = url
    with httpx.Client(timeout=timeout, trust_env=False,
                      limits=httpx.Limits(max_keepalive_connections=0), headers={
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        "Accept-Encoding": "gzip, identity", "User-Agent": "zotero-summarizer/0.1 RSS reader",
    }) as client:
        for _ in range(5):
            with stream_public_url(client, current) as resp:
                if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                resp.raise_for_status()
                return _read_rss_body(resp), resp.headers
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


def _entry_to_item(
    entry: Any, *, feed: dict[str, Any], feed_title: str
) -> dict[str, Any]:
    link = str(entry.get("link") or "").strip()
    entry_id = str(entry.get("id") or "").strip()
    guid = str(entry.get("guid") or entry_id or "").strip()
    summary = str(entry.get("summary") or entry.get("description") or "").strip()
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
        "doi": str(entry.get("doi") or ""),
        "publication_date": str(
            entry.get("published") or entry.get("updated") or ""
        ).strip(),
        "publication_title": str(feed_title or feed.get("name") or "").strip(),
        "authors": _entry_authors(entry),
        "item_type": "journalArticle",
    }
    item = {key: _INJECTION_CHAR_PATTERN.sub("", value).strip() if isinstance(value, str) else value for key, value in item.items()}
    item["doi"] = item["doi"] or doi_from_text(item["entry_id"], item["url"], item["abstract"])
    item["arxiv_id"] = _arxiv_id_from_url_or_doi(item["url"] or item["entry_id"], item["doi"])
    item["stable_feed_key"] = stable_feed_key_from_item(item)
    return item


class AppRssReader:
    """RSS reader that treats the app DB as the subscription/source of truth."""

    def __init__(self, triage_db_path: Path):
        self.triage_db_path = Path(triage_db_path)

    def _conn(self):
        return feeds_storage.open_triage_conn(self.triage_db_path)

    def refresh_feeds(
        self,
        *,
        max_feeds: int = 10,
        max_new_items_per_feed: int = 25,
        per_feed_timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Fetch a bounded pass of enabled feeds and store parsed items.

        Feeds are taken least-recently-fetched-first (never-fetched first), so a
        bounded pass ROTATES through every enabled feed across successive calls.
        The old alphabetical slice permanently starved feeds beyond the first
        ``max_feeds`` (31 of 41 enabled feeds had never been fetched).
        ``record_rss_fetch_result`` stamps ``last_fetched_at`` on failures too,
        so a broken feed cannot hog the rotation.

        This does not score anything and does not call an LLM.
        """
        if offline_requested():
            return {
                "feeds": 0,
                "inserted": 0,
                "updated": 0,
                "errors": [],
                "offline": True,
            }
        import feedparser

        fetched = 0
        inserted = 0
        updated = 0
        with self._conn() as conn:
            feeds = rss_storage.list_rss_feeds(conn)
            # ponytail: LRU rotation via Python sort ('' < any timestamp puts
            # never-fetched first); move into SQL ORDER BY if feed counts grow.
            feeds.sort(key=lambda f: str(f.get("last_fetched_at") or ""))
            feeds = feeds[: max(0, int(max_feeds))]
            for feed in feeds:
                fetched += 1
                try:
                    body, headers = _fetch_public_url(
                        str(feed["url"]), timeout=float(per_feed_timeout)
                    )
                    parsed = feedparser.parse(body)
                    if parsed.bozo or not parsed.version:
                        raise RssUrlRejected("Invalid RSS/Atom feed document")
                    feed_title = str(
                        (parsed.feed or {}).get("title") or feed.get("name") or ""
                    )
                    for entry in list(parsed.entries or [])[
                        : max(0, int(max_new_items_per_feed))
                    ]:
                        item = _entry_to_item(entry, feed=feed, feed_title=feed_title)
                        if not item.get("stable_feed_key"):
                            continue
                        _, was_inserted = rss_storage.upsert_rss_item(
                            conn,
                            rss_feed_id=int(feed["id"]),
                            item=item,
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
                    conn.rollback()
                    rss_storage.record_rss_fetch_result(
                        conn,
                        int(feed["id"]),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    conn.commit()
                    raise
        return {
            "feeds": fetched,
            "inserted": inserted,
            "updated": updated,
            "errors": [],
        }

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
        limit: int | None = 1000,
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
                    "guid": str(
                        row["guid"] or row["entry_id"] or row["stable_feed_key"] or ""
                    ),
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


__all__ = ["AppRssReader", "RssUrlRejected", "validate_rss_url"]
