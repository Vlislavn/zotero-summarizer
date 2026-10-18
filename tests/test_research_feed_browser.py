"""Opt-in Weekly journey reusing the existing built-app Chromium/ASGI fixture.

Synthetic saved reviews are source-bound test inputs, not generated model output.
Run only in the parent's guarded browser slot after the frontend build.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401 — reuse owned fixture
from tests.test_research_feed import _review, _seed
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_APPLICATION_BROWSER_SMOKE") != "1", reason="opt-in built application Chromium check",
)


def test_built_weekly_saved_cards_and_cached_build_mobile_desktop(application_browser):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    page.goto("http://127.0.0.1/weekly")
    expect(page.get_by_role("heading", name="Weekly Research Intelligence", exact=True)).to_be_visible()
    expect(page.get_by_text("No saved weekly digests yet. Build a bounded run above.")).to_be_visible()
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute(
            "INSERT INTO rss_feeds (id, name, url) VALUES (1, 'AI Conference', 'https://example.test/feed')",
        )
    key = stable_feed_key_from_parts(guid="weekly-browser-paper")
    _seed(settings, key, "Weekly agent harness benchmark")
    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    run_id = Path(result["json_path"]).parent.name
    page.get_by_role("button", name="Refresh saved runs", exact=True).click()
    page.get_by_role("combobox", name="Saved digest", exact=True).select_option(run_id)
    expect(page.get_by_role("heading", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Project use", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Reproducibility: unknown", exact=True)).to_be_visible()
    assert page.get_by_role("link", name="Open paper review", exact=True).get_attribute("href") == f"/paper/{key.replace(':', '%3A')}"
    download = client.get(f"/api/research-feed/runs/{run_id}/download?format=json")
    assert download.status_code == 200 and download.json()["cards"][0]["candidate"]["source_id"] == key
    for width in (390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(settings.data_dir / f"weekly-cards-{width}.png"), full_page=True)
    page.reload()
    expect(page.get_by_role("heading", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    page.get_by_label("From (UTC)", exact=True).fill("2026-08-20")
    page.get_by_label("To (inclusive, UTC)", exact=True).fill("2026-08-29")
    page.get_by_role("button", name="Build from saved reviews", exact=True).click()
    expect(page.get_by_text("No engineering cards in this run. Check review/full-text needs, failures and selection details below.")).to_be_visible()
    expect(page.get_by_role("link", name="Weekly agent harness benchmark", exact=True)).to_be_visible()
    assert any(row[:2] == ("POST", "/api/research-feed/run") for row in seen)
    assert not any(row[:2] == ("POST", "/api/library/deep-review/run") for row in seen)
    with sqlite3.connect(settings.triage_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pending_changes").fetchone()[0] == 0
    print("BROWSER_RECEIPT", json.dumps({"journey": "weekly-saved-cards-cached-run", "requests": seen}))
