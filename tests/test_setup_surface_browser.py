"""Opt-in built setup layout/recovery with synthetic Doctor HTTP states, no loads.

Reuse the existing isolated ASGI/Chromium fixture and its parent-guarded build.
These UI observations do not establish real inference, cache or hardware readiness.
"""
import json
import os

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401
from tests.test_connection_browser import connection_build  # noqa: F401 — explicit disposable build

pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_APPLICATION_BROWSER_SMOKE") != "1",
    reason="opt-in built setup Chromium check",
)

_CHECKS = [
    {"id": "environment", "status": "ready", "message": "App environment is writable"},
    {"id": "runtime_model", "status": "running", "message": "Runtime inventory in progress"},
    {"id": "dry_run", "status": "not_started", "message": "Dry-run not checked yet"},
    {"id": "llm_inference", "status": "needs_action", "message": "LLM verification needs action",
     "detail": "local/synthetic-model: effective_envelope_unknown; not submitted",
     "load_receipts": [{"admission": {"status": "unknown"}, "attempted": False}],
     "recovery": {"action": "open_ai_settings", "label": "Review endpoint settings"}},
    {"id": "rss_source", "status": "needs_action", "message": "No RSS source is enabled",
     "recovery": {"action": "open_sources_settings", "label": "Add an RSS source"}},
    {"id": "optional_extras", "status": "unavailable", "message": "Browser extras are optional"},
]


@pytest.mark.parametrize("destination,label,heading,control", [
    ("/today?task=rss", "Add an RSS source", "RSS sources", "#rss-feeds button"),
    ("/settings?task=connection", "Review endpoint settings", "AI models", "#ai-models button"),
], ids=["sources", "ai"])
@pytest.mark.parametrize("width", [390, 1024, 1440])
def test_setup_health_states_reflow_and_keyboard_recovery(application_browser, width, destination, label, heading, control):
    from patchright.sync_api import expect

    page, _client, settings, seen = application_browser
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script("localStorage.setItem('zs_setup_progress_v1', JSON.stringify({mode:'local', step:4, draft:null}));")
    doctor_reads = []
    state = {"ready": False, "status": "running", "checks": [dict(row) for row in _CHECKS],
             "modes": {"local_inference": "needs_action", "offline_ready": "needs_action",
                       "strict_offline": "not_started"}}

    def doctor_state(route):
        assert route.request.method == "GET", "Layout/recovery cannot start verification"
        doctor_reads.append(route.request.method)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(state))

    page.route("**/api/setup/doctor", doctor_state)
    page.goto("http://127.0.0.1/setup")
    expect(page.get_by_role("heading", name="Setup saved", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Open Today", exact=True)).to_be_disabled()
    checklist = page.get_by_role("list", name="Setup checks")
    other_checks = checklist.get_by_text("1 check passed · 1 unavailable", exact=True)
    other_checks.focus()
    other_checks.press("Enter")
    for status in ("Not started", "Running", "Ready", "Needs action", "Unavailable"):
        expect(checklist.get_by_text(status, exact=True).first).to_be_visible()
    expect(page.get_by_text("Local inference", exact=True)).to_be_visible()
    expect(page.get_by_text("Offline-ready", exact=True)).to_be_visible()
    expect(page.get_by_text("Strict offline", exact=True)).to_be_visible()
    expect(page.get_by_text("Local load safety is not established", exact=False)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(settings.data_dir / f"setup-health-{width}.png"), full_page=True, animations="disabled")
    expect(page.get_by_role("button", name="Verifying…", exact=True)).to_be_disabled()
    state["status"] = "needs_action"
    state["checks"][1].update(status="ready", message="Runtime inventory finished")
    page.reload()

    recovery = page.get_by_role("link", name=label, exact=True)
    recovery.focus()
    recovery.press("Enter")
    expect(page).to_have_url("http://127.0.0.1" + destination)
    expect(page.get_by_role("heading", name=heading, exact=True, level=1)).to_be_focused()
    expect(page.locator(control).first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.go_back()
    expect(page.get_by_role("heading", name="Setup saved", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Zotero paths", exact=True)).to_have_attribute("href", "/settings?task=zotero")
    inputs = page.get_by_role("link", name="RSS sources", exact=True)
    expect(inputs).to_have_attribute("href", "/today?task=rss")
    inputs.focus()
    inputs.press("Enter")
    expect(page.get_by_role("heading", name="RSS sources", exact=True, level=1)).to_be_focused()
    page.get_by_role("link", name="Back to Setup", exact=True).click()
    expect(page).to_have_url("http://127.0.0.1/setup")
    expect(page.get_by_role("heading", name="Setup saved", exact=True)).to_be_visible()
    assert doctor_reads
    assert not [row for row in seen if row[0] == "POST" and row[1] in {
        "/api/setup/doctor", "/api/admin/llm-models", "/api/rss/refresh",
    }]
    print("BROWSER_RECEIPT", json.dumps({"journey": "synthetic-setup-health-keyboard-recovery",
                                        "width": width, "recovery": destination,
                                        "states": 5, "model_loads": "not requested"}))


@pytest.mark.parametrize("width", [320, 390])
def test_setup_progress_reflows_with_accessible_states(application_browser, width):
    from patchright.sync_api import expect

    page, _client, settings, _seen = application_browser
    page.set_viewport_size({"width": width, "height": 844})
    page.goto("http://127.0.0.1/setup")
    expect(page.get_by_role("heading", name="Set up Zotero Summarizer", exact=True)).to_be_visible()
    progress = page.get_by_role("list", name="Setup progress")
    current = progress.locator('[aria-current="step"]')
    expect(current).to_have_count(1)
    expect(current).to_contain_text("Current step")
    assert "Not complete" in progress.locator(".sr-only").all_text_contents()
    page.screenshot(path=str(settings.data_dir / f"setup-progress-{width}.png"), full_page=True, animations="disabled")
    assert page.evaluate("document.documentElement.scrollWidth === document.documentElement.clientWidth")
