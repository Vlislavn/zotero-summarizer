"""Opt-in saved Weekly journey using the existing built-app ASGI/Chromium fixture.

Parent runs this only in its guarded sequential browser slot after the UI build.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401 — shared fixture
from tests.test_research_feed import _review, _seed
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_APPLICATION_BROWSER_SMOKE") != "1", reason="opt-in built application Chromium check",
)


def test_built_weekly_saved_cards_downloads_errors_and_paper_navigation(application_browser):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    page.goto("http://127.0.0.1/weekly")
    expect(page.get_by_role("heading", name="Weekly Research Intelligence", exact=True)).to_be_visible()
    expect(page.get_by_text("No saved weekly digests yet.", exact=False)).to_be_visible()
    assert page.get_by_role("link", name="Weekly", exact=True).get_attribute("href") == "/weekly"
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute("INSERT INTO rss_feeds (id, name, url) VALUES (1, 'Weekly test', 'https://example.test/feed')")
    key = stable_feed_key_from_parts(guid="weekly-browser-paper")
    _seed(settings, key, "Weekly agent harness benchmark")
    args = dict(start=datetime(2026, 8, 20, tzinfo=timezone.utc),
                end=datetime(2026, 8, 29, tzinfo=timezone.utc))
    result = run_weekly(settings, **args, review_loader=_review)
    path = Path(result["json_path"])
    run_id = path.parent.name
    page.get_by_role("button", name="Refresh saved runs", exact=True).click()
    page.get_by_role("combobox", name="Saved digest", exact=True).select_option(run_id)
    expect(page.get_by_role("heading", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Project use", exact=True)).to_be_visible()
    paper = page.get_by_role("link", name="Open paper review", exact=True)
    assert paper.get_attribute("href") == f"/paper/{key.replace(':', '%3A')}?autoRun=false"
    for extension in ("json", "md"):
        link = page.get_by_role("link", name="Download canonical JSON" if extension == "json" else "Download Markdown")
        download = client.get(link.get_attribute("href"))
        assert download.status_code == 200
        assert download.content == path.with_suffix(f".{extension}").read_bytes()
    for width in (390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.reload()
    expect(page.get_by_role("heading", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    paper.click()
    expect(page.get_by_role("heading", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    expect(page).to_have_url(f"http://127.0.0.1/paper/{key.replace(':', '%3A')}?autoRun=false")
    page.go_back()
    expect(page.get_by_role("combobox", name="Saved digest", exact=True)).to_have_value(run_id)

    legacy = json.loads(path.read_text())
    del legacy["cards"][0]["provenance"]
    del legacy["research_ideas_by_project"]
    path.write_text(json.dumps(legacy))
    page.get_by_role("button", name="Refresh saved runs", exact=True).click()
    expect(page.get_by_text("Source basis: unknown", exact=False)).to_be_attached()
    empty = run_weekly(settings, **args, review_loader=lambda _: None)
    page.get_by_role("button", name="Refresh saved runs", exact=True).click()
    page.get_by_role("combobox", name="Saved digest", exact=True).select_option(Path(empty["json_path"]).parent.name)
    expect(page.get_by_text("No engineering cards in this run.", exact=False)).to_be_visible()
    expect(page.get_by_role("link", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    path.write_text("not JSON")
    page.get_by_role("combobox", name="Saved digest", exact=True).select_option(run_id)
    page.get_by_role("button", name="Refresh saved runs", exact=True).click()
    expect(page.get_by_role("alert")).to_be_visible()
    expect(page.get_by_text("No engineering cards in this run.", exact=False)).to_have_count(0)
    assert not any(method == "POST" and route.startswith(("/api/research-feed", "/api/library/deep-review"))
                   for method, route, _ in seen)
    with sqlite3.connect(settings.triage_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pending_changes").fetchone()[0] == 0
    print("BROWSER_RECEIPT", json.dumps({"journey": "weekly-read-only-saved-browser", "requests": seen}))
