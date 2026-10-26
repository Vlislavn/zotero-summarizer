"""Opt-in actual RSS TCP/SQLite writes with synthetic Doctor GETs; no model work.

Reuse the normal conftest isolation and existing owned-browser fixture. A buffered
real response followed by route.abort is a lost-ack discriminator, not packet loss.
"""
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import _review_question_browser as support
from tests import test_review_question_browser as journeys

question_browser = journeys.question_browser
pytestmark = journeys.pytestmark
READY = {"ready": True, "status": "ready", "config_signature": "synthetic-before",
         "checks": [], "modes": {}}


def _rows(lab):
    return support.db_rows(lab.database, "SELECT * FROM rss_feeds ORDER BY id")


def _doctor_route(lab, before, observations):
    def route(request):
        path = urlsplit(request.request.url).path
        if path == "/api/setup/doctor":
            assert request.request.method == "GET", "No actual Doctor/model execution permitted"
            now = _rows(lab)
            observations.append({"path": path, "rows": now})
            body = READY if now == before else {"ready": False, "status": "not_started",
                "config_signature": "synthetic-after", "modes": {}, "checks": [
                    {"id": "rss_source", "status": "not_started", "message": "Source changed; verify again"}]}
            request.fulfill(status=200, json=body)
        elif path in {"/api/config", "/api/setup/status", "/api/rss/feeds"}:
            assert request.request.method == "GET"
            request.fallback()
        else:
            observations.append({"blocked_unrelated_api": path, "method": request.request.method})
            request.fulfill(status=503, json={"error": "probe_scope", "message": "Unrelated operation disabled"})
    return route


def _write_route(lab, outcome, before, fault):
    def route(request):
        assert request.request.method == "DELETE"
        reply = None
        try:
            fault.update(method=request.request.method, url=request.request.url)
            options = {"max_redirects": 0, "max_retries": 0, "timeout": support.ACTION_TIMEOUT_MS}
            if outcome == "rejected":
                options["headers"] = {**request.request.all_headers(), "origin": "https://untrusted.invalid"}
                fault["control_boundary"] = "Only Origin header altered; actual prewrite middleware rejection"
            reply = request.fetch(**options)
            fault.update(http_status=reply.status, actual_reply=reply.json(), after_rows=_rows(lab))
            if outcome == "rejected":
                assert reply.status == 403 and _rows(lab) == before
                request.fulfill(response=reply)
            else:
                assert reply.status == 200 and _rows(lab) == []
                assert fault["actual_reply"] == {"deleted": True}
                request.abort("connectionreset")
                fault["browser_reply_aborted"] = True
        finally:
            if reply is not None:
                reply.dispose()
            support.write_json(lab.artifact / "rss-fault.json", fault)
    return route


def _retain(lab, page, outcome, before, fault, observations):
    writes = [r for r in lab.server.receipts if r["method"] == "DELETE"
              and r["path"] == urlsplit(fault.get("url", "")).path]
    sources = [support.ROOT / name for name in (
        "frontend/src/components/settings/RssFeedsSection.jsx",
        "frontend/src/utils/setupDoctorState.js",
    )] + [Path(__file__)]
    evidence = {"outcome": outcome, "before_rows": before, "after_rows": _rows(lab), "fault": fault,
        "doctor_observations": observations, "actual_server_writes": writes,
        "ready_visible_after_error": page.get_by_text("✓ Setup verified", exact=True).is_visible(),
        "boundary": "Actual TCP/SQLite RSS; synthetic Doctor GET only; route.fetch then abort is not packet loss",
        "source_hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    support.write_json(lab.artifact / "rss-uncertainty-result.json", evidence)
    lab.checkpoint("rss-uncertainty-" + outcome, page)


@pytest.mark.parametrize("outcome", ["rejected", "committed_reply_lost"])
def test_rss_write_uncertainty_does_not_reuse_verified_health(question_browser, outcome, record_property):
    from patchright.sync_api import expect

    lab = question_browser
    before = _rows(lab)
    assert len(before) == 1
    observations, fault = [], {}
    page = lab.open_browser()
    lab.context.route(lab.server.origin + "/api/**", _doctor_route(lab, before, observations))
    page.goto(lab.server.origin + "/settings#rss-feeds")
    expect(page.get_by_text("✓ Setup verified", exact=True)).to_be_visible()
    region = page.locator('#rss-feeds')
    expect(region.get_by_role("button", name="Save", exact=True)).to_be_visible()
    target = lab.server.origin + "/api/rss/feeds/" + str(before[0]["id"])
    lab.context.route(target, _write_route(lab, outcome, before, fault))
    terminal = (page.expect_response(lambda r: r.url == target) if outcome == "rejected" else
                page.expect_event("requestfailed", predicate=lambda r: r.url == target))
    with terminal:
        region.get_by_role("button", name="Delete", exact=True).click()
    expect(region.get_by_role("status")).to_be_visible()  # original error stays visible
    writes = [r for r in lab.server.receipts if r["method"] == "DELETE" and r["path"] == urlsplit(target).path]
    record_property("rss_uncertainty_artifact", str(lab.artifact))
    try:
        assert fault["http_status"] == (403 if outcome == "rejected" else 200)
        assert len(writes) == 1 and writes[0]["status"] == fault["http_status"]
        if outcome == "rejected":
            assert _rows(lab) == before
            expect(page.get_by_text("✓ Setup verified", exact=True)).to_be_visible()
            expect(region.get_by_role("button", name="Save", exact=True)).to_be_visible()
            assert len([r for r in observations if r.get("path") == "/api/setup/doctor"]) == 1
        else:
            assert fault["browser_reply_aborted"] and _rows(lab) != before
            expect(page.get_by_text("✓ Setup verified", exact=True)).not_to_be_visible()
            expect(page.get_by_text("Source changed; verify again", exact=True)).to_be_visible()
            expect(region.get_by_text("No RSS feeds saved.", exact=True)).to_be_visible()
            expect(region.get_by_role("button", name="Save", exact=True)).to_have_count(0)
            reads = [r for r in observations if r.get("path") == "/api/setup/doctor"]
            assert len(reads) == 2 and reads[-1]["rows"] == []
        feed_reads = [r for r in lab.server.receipts if r["method"] == "GET" and r["path"] == "/api/rss/feeds"]
        assert len(feed_reads) == (1 if outcome == "rejected" else 2)
        assert not [r for r in lab.requests if r["method"] == "POST" and urlsplit(r["url"]).path in {
            "/api/setup/doctor", "/api/admin/llm-models", "/api/rss/refresh",
        }]
        assert len([r for r in lab.requests if r["method"] == "DELETE" and r["url"] == target]) == 1
    finally:
        _retain(lab, page, outcome, before, fault, observations)
