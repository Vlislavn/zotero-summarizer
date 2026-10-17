"""Opt-in built setup layout/recovery with synthetic Doctor HTTP states, no loads.

Reuse the existing isolated ASGI/Chromium fixture and its parent-guarded build.
These UI observations do not establish real inference, cache or hardware readiness.
"""
import json
import os

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401

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
     "recovery": {"action": "open_sources_settings", "label": "Add a source in Settings"}},
    {"id": "optional_extras", "status": "unavailable", "message": "Browser extras are optional"},
]


@pytest.mark.parametrize("width", [390, 1024, 1440])
def test_setup_health_states_reflow_and_keyboard_recovery(application_browser, width):
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
    for status in ("Not started", "Running", "Ready", "Needs action", "Unavailable"):
        expect(checklist.get_by_text(status, exact=True).first).to_be_visible()
    expect(page.get_by_text("Local inference", exact=True)).to_be_visible()
    expect(page.get_by_text("Offline-ready", exact=True)).to_be_visible()
    expect(page.get_by_text("Strict offline", exact=True)).to_be_visible()
    expect(page.get_by_text("Local load safety is not established", exact=False)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(settings.data_dir / f"setup-health-{width}.png"), full_page=True)
    expect(page.get_by_role("button", name="Verifying…", exact=True)).to_be_disabled()
    state["status"] = "needs_action"
    state["checks"][1].update(status="ready", message="Runtime inventory finished")
    page.reload()

    recovery = page.get_by_role("button", name="Add a source in Settings", exact=True)
    recovery.focus()
    recovery.press("Enter")
    expect(page).to_have_url("http://127.0.0.1/settings#rss-feeds")
    expect(page.locator("#rss-feeds button").first).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert doctor_reads
    assert not [row for row in seen if row[0] == "POST" and row[1] in {
        "/api/setup/doctor", "/api/admin/llm-models", "/api/rss/refresh",
    }]
    print("BROWSER_RECEIPT", json.dumps({"journey": "synthetic-setup-health-keyboard-recovery",
                                        "width": width, "states": 5, "model_loads": "not requested"}))
