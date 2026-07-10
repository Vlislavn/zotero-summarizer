"""App-owned RSS feed management endpoints."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.app_rss import AppRssReader, RssUrlRejected, validate_rss_url
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import rss as rss_storage


router = APIRouter()


class RssFeedRequest(BaseModel):
    name: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    enabled: bool = True


class RssFeedUpdateRequest(BaseModel):
    name: str | None = Field(default=None)
    url: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)


class RssRefreshRequest(BaseModel):
    max_feeds: int = Field(default=10, ge=1, le=100)
    max_new_items_per_feed: int = Field(default=25, ge=1, le=500)
    per_feed_timeout_secs: float = Field(default=10.0, ge=1.0, le=60.0)


def _clean_feed(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out.get("enabled"))
    return out


async def list_feeds() -> dict[str, Any]:
    def _read() -> list[dict[str, Any]]:
        with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
            return [_clean_feed(row) for row in rss_storage.list_rss_feeds(conn, include_disabled=True)]

    feeds = await asyncio.to_thread(_read)
    return {"feeds": feeds, "total": len(feeds)}


async def add_feed(req: RssFeedRequest) -> dict[str, Any]:
    try:
        safe_url = validate_rss_url(req.url)
    except RssUrlRejected as exc:
        raise APIError("validation_error", str(exc), status_code=422) from exc

    def _write() -> dict[str, Any]:
        with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
            feed_id = rss_storage.upsert_rss_feed(
                conn, name=req.name, url=safe_url, enabled=req.enabled, source="app",
            )
            conn.commit()
            row = conn.execute("SELECT * FROM rss_feeds WHERE id = ?", (feed_id,)).fetchone()
            return _clean_feed(dict(row))

    return await asyncio.to_thread(_write)


async def update_feed(feed_id: int, req: RssFeedUpdateRequest) -> dict[str, Any]:
    safe_url = None
    if req.url is not None:
        try:
            safe_url = validate_rss_url(req.url)
        except RssUrlRejected as exc:
            raise APIError("validation_error", str(exc), status_code=422) from exc

    def _write() -> dict[str, Any]:
        with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
            ok = rss_storage.update_rss_feed(
                conn,
                int(feed_id),
                name=req.name,
                url=safe_url,
                enabled=req.enabled,
            )
            if not ok:
                raise APIError("not_found", f"rss feed id={feed_id} not found", status_code=404)
            conn.commit()
            row = conn.execute("SELECT * FROM rss_feeds WHERE id = ?", (int(feed_id),)).fetchone()
            return _clean_feed(dict(row))

    return await asyncio.to_thread(_write)


async def delete_feed(feed_id: int) -> dict[str, Any]:
    def _write() -> dict[str, Any]:
        with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
            deleted = rss_storage.delete_rss_feed(conn, int(feed_id))
            conn.commit()
            return {"deleted": deleted}

    return await asyncio.to_thread(_write)


async def import_from_zotero() -> dict[str, Any]:
    def _import() -> dict[str, Any]:
        reader = ZoteroReader(get_settings().zotero_data_dir)
        groups = reader.get_feed_groups()
        with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
            result = rss_storage.import_zotero_feeds(conn, groups)
            conn.commit()
            return result

    try:
        return await asyncio.to_thread(_import)
    except Exception as exc:
        raise APIError(
            "zotero_unavailable",
            f"could not import Zotero RSS feeds: {exc}",
            status_code=503,
        ) from exc


async def refresh_feeds(req: RssRefreshRequest) -> dict[str, Any]:
    reader = AppRssReader(get_settings().triage_db_path)
    return await asyncio.to_thread(
        reader.refresh_feeds,
        max_feeds=req.max_feeds,
        max_new_items_per_feed=req.max_new_items_per_feed,
        per_feed_timeout=req.per_feed_timeout_secs,
    )


router.add_api_route("/api/rss/feeds", list_feeds, methods=["GET"])
router.add_api_route("/api/rss/feeds", add_feed, methods=["POST"])
router.add_api_route("/api/rss/feeds/{feed_id}", update_feed, methods=["PUT"])
router.add_api_route("/api/rss/feeds/{feed_id}", delete_feed, methods=["DELETE"])
router.add_api_route("/api/rss/import-zotero", import_from_zotero, methods=["POST"])
router.add_api_route("/api/rss/refresh", refresh_feeds, methods=["POST"])
