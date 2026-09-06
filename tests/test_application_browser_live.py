"""Opt-in built React → real ASGI/SQLite journeys, without a live service/profile.

Run after npm run build with ZS_APPLICATION_BROWSER_SMOKE=1 pytest -q -s this_file.
Chromium requests are transported through TestClient; no API response is mocked.
AI, training, network sources and background recovery are disabled explicitly.
"""
from functools import partial
import json
import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from zotero_summarizer.api.app import create_app
from zotero_summarizer.services import lifecycle
from zotero_summarizer.services._common import read_config, write_user_config
from zotero_summarizer.services.setup import bootstrap, detect
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds, repositories
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts


pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_APPLICATION_BROWSER_SMOKE") != "1", reason="opt-in built application Chromium check",
)


@pytest.fixture
def application_browser(tmp_path, monkeypatch):
    from patchright.sync_api import sync_playwright

    for section in ("CORPUS", "CLASSIFIER_GATE", "PRESTIGE", "FULL_TEXT_REFINE", "OPENREVIEW"):
        monkeypatch.setenv(f"ZS_{section}_ENABLED", "0")
    monkeypatch.setenv("ZS_OFFLINE", "1")
    monkeypatch.setenv("APP_LOG_FILE", "browser-smoke.log")
    monkeypatch.setenv("PDF_ROOT", str(tmp_path / "pdfs"))
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(tmp_path / "missing-zotero"))
    monkeypatch.setattr(detect, "_platform_candidate_dirs", lambda: [])
    settings = Settings.load(project_root=tmp_path)
    bootstrap.bootstrap_phase0(settings)
    config = read_config(settings.config_path)
    config.llm_enabled = False
    write_user_config(settings.config_path, config)
    monkeypatch.setattr(lifecycle, "startup", partial(lifecycle.startup, background=False))
    seen, errors, blocked = [], [], []
    with TestClient(create_app(settings), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")

            def transport(route):
                request = route.request
                url = urlsplit(request.url)
                if url.netloc != "127.0.0.1":
                    blocked.append(request.url)
                    route.abort()
                    return
                response = client.request(
                    request.method, request.url, content=request.post_data_buffer,
                    headers=request.headers,
                )
                seen.append((request.method, url.path, response.status_code))
                if response.status_code == 503 and url.path in {"/api/zotero/collections", "/api/zotero/tags"}:
                    assert response.json()["error"] == "zotero_unavailable"
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

            context.route("**/*", transport)
            page = context.new_page()
            page.set_default_timeout(8000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                yield page, client, settings, seen
                assert errors == [], errors
                assert blocked == [], blocked
                assert not [row for row in seen if row[2] >= 500
                            and row not in {("GET", "/api/zotero/collections", 503),
                                            ("GET", "/api/zotero/tags", 503)}], seen
            finally:
                context.close()
                browser.close()


def test_built_application_mobile_verdict_survives_reload(application_browser):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    key = stable_feed_key_from_parts(guid="browser-paper")
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(
            conn, run_id="browser-smoke", decision=feeds.DECISION_USER_APPROVED,
            feed_item={"feed_library_id": 1, "item_id": 1, "guid": "browser-paper", "title": "Browser paper"},
            composite_score=4.5,
        )
        conn.commit()
    response = client.post("/api/golden/verdict", json={
        "item_key": key, "user_priority": "must_read", "comment": "critical rationale",
    })
    assert response.status_code == 200, response.text
    page.goto(f"http://127.0.0.1/paper/{key}")
    expect(page.get_by_role("heading", name="Browser paper", exact=True)).to_be_visible()
    jump = page.get_by_role("link", name="Your verdict", exact=True)
    expect(jump).to_be_visible()
    jump.click()
    page.get_by_role("button", name="Edit", exact=True).click()
    comment = page.get_by_placeholder("Why? (free text — used to detect patterns later)")
    expect(comment).to_have_value("critical rationale")
    comment.fill("unsaved draft")
    page.get_by_role("button", name="Should read", exact=True).click()
    jump.click()
    expect(comment).to_have_value("unsaved draft")
    page.get_by_role("button", name="Cancel", exact=True).click()
    assert not [row for row in seen if row[:2] == ("POST", "/api/golden/verdict")]
    assert repositories.get_label_verdict(settings.triage_db_path, key)["comment"] == "critical rationale"
    page.get_by_role("button", name="Edit", exact=True).click()
    page.get_by_role("button", name="Could read", exact=True).click()
    page.get_by_role("button", name="Update", exact=True).click()
    expect(page.get_by_role("button", name="Edit", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("“critical rationale”", exact=True)).to_be_visible()
    saved = repositories.get_label_verdict(settings.triage_db_path, key)
    assert (saved["user_priority"], saved["comment"]) == ("could_read", "critical rationale")
    page.screenshot(path=str(settings.data_dir / "mobile-verdict.png"), full_page=True)
    print("BROWSER_RECEIPT", json.dumps({"journey": "mobile-verdict-reload", "requests": seen}))


def test_built_application_setup_routes_and_filtered_pending(application_browser):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    page.goto("http://127.0.0.1/")
    expect(page.get_by_role("heading", name="Set up Zotero Summarizer")).to_be_visible()
    page.get_by_role("button", name="Skip for now").click()
    expect(page).to_have_url("http://127.0.0.1/library")
    for path, heading in (("/today", "Today’s reading"), ("/search", "Targeted Search"), ("/settings", "Settings")):
        page.goto("http://127.0.0.1" + path)
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
    for index in range(4):
        repositories.insert_pending_changes(
            f"P{index}", "Alpha" if index == 0 else f"Beta {index}",
            [{"change_type": "add_to_collection", "payload": {"collection_key": "C1"}}]
            if index == 0 else [{"change_type": "add_note", "payload": {"note": "Synthetic note"}}],
        )
    page.goto("http://127.0.0.1/pending")
    expect(page).to_have_url("http://127.0.0.1/ops?tab=pending")
    page.get_by_role("button", name="Select all", exact=True).click()
    page.get_by_placeholder("Filter by title…").fill("Alpha")
    page.get_by_role("combobox").focus()
    page.keyboard.press("a")
    expect(page.get_by_role("checkbox")).to_be_checked()
    assert not [row for row in seen if row[:2] == ("POST", "/api/pending/apply")]
    page.get_by_role("button", name="Reject selected", exact=True).click()
    expect(page.get_by_text("Selected changes rejected.", exact=True)).to_be_visible()
    pending = client.get("/api/pending?status=pending").json()["items"]
    rejected = client.get("/api/pending?status=rejected").json()["items"]
    assert {row["item_key"] for row in pending} == {"P1", "P2", "P3"}
    assert {row["item_key"] for row in rejected} == {"P0"}
    page.reload()
    expect(page.get_by_text("Beta 1", exact=True)).to_be_visible()
    expect(page.get_by_text("Alpha", exact=True)).to_have_count(0)
    print("BROWSER_RECEIPT", json.dumps({"journey": "setup-routes-filtered-pending", "requests": seen}))


def test_built_search_plan_and_same_title_adds_survive_reload(application_browser, monkeypatch):
    from unittest.mock import Mock
    from patchright.sync_api import expect
    from zotero_summarizer.services.search import materialize, session
    from zotero_summarizer.services.search._models import Candidate, QueryPlan, SearchIntent

    page, _, _, seen = application_browser
    research = session.new_session(raw_query="Browser search", intent=SearchIntent(raw_query="Browser search"),
                                   plan=QueryPlan(arxiv_variants=['"tight topic"', "broad topic"],
                                                  openreview="peer-review topic"), questions=[])
    research.status = "reviewed"
    research.candidates = [Candidate(title="Same browser title", authors=[name]) for name in ("Alice", "Bob")]
    session.save(research)
    writer = Mock()
    monkeypatch.setattr(materialize, "ZoteroWriter", Mock(return_value=writer))
    page.goto("http://127.0.0.1/search")
    page.evaluate("id => sessionStorage.setItem('zs.searchSession', JSON.stringify({id}))", research.id)
    page.reload()
    page.get_by_text("Query plan (per source)", exact=True).click()
    for query in ('"tight topic"', "broad topic", "peer-review topic"):
        expect(page.get_by_text(query, exact=True)).to_be_visible()
    adds = page.get_by_role("button", name="Add to library", exact=True)
    expect(adds).to_have_count(2)
    adds.nth(0).click()
    expect(adds).to_have_count(1)
    adds.nth(0).click()
    expect(page.get_by_text("✓ In library", exact=True)).to_have_count(2)
    page.reload()
    expect(page.get_by_text("✓ In library", exact=True)).to_have_count(2)
    assert [call.kwargs["feed_payload"]["authors"] for call in writer.apply_feed_materialization.call_args_list] == [
        ["Alice"], ["Bob"]]
    persisted = session.load(research.id).candidates
    assert len({candidate.candidate_id for candidate in persisted}) == 2
    assert len({candidate.materialized_zotero_key for candidate in persisted}) == 2
    print("BROWSER_RECEIPT", json.dumps({"journey": "search-plan-two-adds-reload", "requests": seen}))
