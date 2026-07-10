from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import rss as rss_routes
from zotero_summarizer.integrations.app_rss import RssUrlRejected


def _settings(tmp_path):
    return SimpleNamespace(
        triage_db_path=tmp_path / "triage.db",
        zotero_data_dir=tmp_path / "zotero",
    )


def test_rss_feed_routes_crud(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rss_routes, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(rss_routes, "validate_rss_url", lambda url: str(url).strip())

    created = asyncio.run(
        rss_routes.add_feed(
            rss_routes.RssFeedRequest(
                name="Example",
                url="https://example.com/rss",
                enabled=True,
            )
        )
    )
    assert created["name"] == "Example"
    assert created["enabled"] is True

    listed = asyncio.run(rss_routes.list_feeds())
    assert listed["total"] == 1
    assert listed["feeds"][0]["url"] == "https://example.com/rss"

    updated = asyncio.run(
        rss_routes.update_feed(
            int(created["id"]),
            rss_routes.RssFeedUpdateRequest(enabled=False, name="Example Updated"),
        )
    )
    assert updated["name"] == "Example Updated"
    assert updated["enabled"] is False

    deleted = asyncio.run(rss_routes.delete_feed(int(created["id"])))
    assert deleted == {"deleted": True}
    assert asyncio.run(rss_routes.list_feeds())["total"] == 0


def test_rss_feed_route_rejects_unsafe_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rss_routes, "get_settings", lambda: _settings(tmp_path))

    def reject(url: str) -> str:
        raise RssUrlRejected("blocked")

    monkeypatch.setattr(rss_routes, "validate_rss_url", reject)
    with pytest.raises(APIError) as excinfo:
        asyncio.run(
            rss_routes.add_feed(
                rss_routes.RssFeedRequest(name="Local", url="file:///tmp/rss.xml")
            )
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.error == "validation_error"


def test_rss_refresh_route_uses_bounded_app_reader(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rss_routes, "get_settings", lambda: _settings(tmp_path))
    seen: dict[str, object] = {}

    class _Reader:
        def __init__(self, db_path):
            seen["db_path"] = db_path

        def refresh_feeds(self, *, max_feeds, max_new_items_per_feed, per_feed_timeout):
            seen.update(
                max_feeds=max_feeds,
                max_new_items_per_feed=max_new_items_per_feed,
                per_feed_timeout=per_feed_timeout,
            )
            return {"feeds": max_feeds, "inserted": 0, "updated": 0, "errors": []}

    monkeypatch.setattr(rss_routes, "AppRssReader", _Reader)
    out = asyncio.run(
        rss_routes.refresh_feeds(
            rss_routes.RssRefreshRequest(
                max_feeds=3,
                max_new_items_per_feed=4,
                per_feed_timeout_secs=5,
            )
        )
    )

    assert out["feeds"] == 3
    assert seen == {
        "db_path": tmp_path / "triage.db",
        "max_feeds": 3,
        "max_new_items_per_feed": 4,
        "per_feed_timeout": 5.0,
    }
