"""Owned Chrome/TCP/SQLite PWA journeys, not physical iOS or remote acceptance."""
from copy import deepcopy
import errno
import json
import socket
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest

from tests import _review_question_browser as support
from tests import test_review_question_browser as questions
from zotero_summarizer.storage import repositories

question_browser = questions.question_browser
pytestmark = questions.pytestmark
KEY = support.LIBRARY_KEY
TITLE = "Synthetic library paper"
MANUAL = "Offline manual body <preserve> & 😀"


def _ready(lab, record_property):
    from patchright.sync_api import expect

    record_property("offline_browser_artifact", str(lab.artifact))
    page = lab.open_browser()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(lab.server.origin + "/today")
    expect(page.get_by_role("heading", name="Today’s reading", exact=True)).to_be_visible()
    support.wait_for(lambda: any(row["item_key"] == KEY for row in support.indexeddb(page)["papers"]))
    support.wait_for(lambda: not support.indexeddb(page)["mutations"])
    assert support.main_world(page, "() => navigator.serviceWorker.ready.then(() => true)") is True
    assert support.main_world(page, "() => Boolean(navigator.serviceWorker.controller)") is True
    page.wait_for_function("""async () => {
      const cache = await caches.open('zs-app-shell-v1');
      return (await cache.keys()).some(row => row.url.includes('/assets/'));
    }""")
    return page


def _verdict(page, label, comment):
    from patchright.sync_api import expect

    panel = page.get_by_role("heading", name="Your verdict", exact=True).locator("..")
    edit = panel.get_by_role("button", name="Edit", exact=True)
    if edit.count():
        edit.click()
    panel.get_by_role("button", name=label, exact=True).click()
    panel.get_by_placeholder("Why? (free text — used to detect patterns later)").fill(comment)
    save = panel.get_by_role("button", name="Update", exact=True)
    (save if save.count() else panel.get_by_role("button", name="Save verdict", exact=True)).click()
    expect(panel.get_by_role("button", name="Edit", exact=True)).to_be_visible()
    expect(panel.get_by_text("Saved on this device — it will sync when the server is reachable", exact=True)).to_be_visible()


def _note(page):
    from patchright.sync_api import expect

    questions._notes_editor(page).fill(MANUAL)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(questions._notes_editor(page)).to_have_value(MANUAL)
    expect(page.get_by_text("Saved on this device.", exact=True)).to_be_visible()
    support.wait_for(lambda: any(row["field"] == "review_note" and row["value"] == MANUAL
                                for row in support.indexeddb(page)["mutations"]))


def _push(lab, body):
    with httpx.Client(base_url=lab.server.origin, trust_env=False) as client:
        response = client.post("/api/sync/push", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["protocol"] == 1
    return result


def _settled(lab, page):
    from patchright.sync_api import expect

    support.wait_for(lambda: not support.indexeddb(page)["mutations"])
    expect(page.get_by_text("Connected to the app", exact=False)).to_be_visible()
    assert not lab.page_errors and not lab.blocked
    return support.db_rows(lab.database, "SELECT * FROM sync_mutations ORDER BY mutation_id")


def test_cold_offline_library_popup_ordered_verdict_note_restart_and_uuid_replay(
        question_browser, record_property):
    from patchright.sync_api import expect

    lab = question_browser
    page = _ready(lab, record_property)
    first_device = support.indexeddb(page)
    assert not any(row["key"].startswith("response:/api/library/reading-queue") for row in first_device["meta"])
    baseline = support.db_rows(lab.database, "SELECT * FROM sync_changes ORDER BY revision")
    lab.context.set_offline(True)
    questions._assert_offline_transport(lab, page)
    lab.close_browser()
    lab.server.stop()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(support.WAIT_SECONDS)
        stopped_connect = probe.connect_ex(("127.0.0.1", lab.server.port))
    support.write_json(lab.artifact / "offline19-stopped-listener.json", {"connect_errno": stopped_connect})
    assert stopped_connect == errno.ECONNREFUSED, "Stopped owned server still accepts TCP connections"
    before_requests = len(lab.server.receipts)
    page = lab.open_browser(offline=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(lab.server.origin + "/library")
    expect(page.get_by_role("button").filter(has_text=TITLE).first).to_be_visible()
    expect(page.get_by_text("Cached working set — verdicts and notes save on this device. Review, meaning search and Zotero export need the Mac.", exact=True)).to_be_visible()
    assert len(lab.server.receipts) == before_requests
    page.get_by_role("button").filter(has_text=TITLE).first.click()
    expect(questions._notes_editor(page)).to_have_value("Initial manual note")
    notice = page.get_by_role('status').filter(has_text='Compact synced context —')
    expect(notice).to_be_visible()
    expect(notice).to_have_text(
        'Compact synced context — summary and abstract may be abbreviated. The full review needs the Mac.')
    lab.context.on("page", lambda tab: tab.on("pageerror", lambda error: lab.page_errors.append(str(error))))
    with lab.context.expect_page() as opened:
        page.get_by_role("button", name="Open full review ↗", exact=True).click()
    review = opened.value
    expect(review.get_by_role("heading", name=TITLE, exact=True)).to_be_visible()
    assert review.url == lab.server.origin + "/paper/" + quote(KEY)
    assert len(lab.server.receipts) == before_requests
    assert support.main_world(review, "() => Boolean(navigator.serviceWorker.controller)") is True
    _verdict(review, "Should read", "First explicit intent")
    _verdict(review, "Could read", "Second explicit intent")
    _verdict(review, "Remove ❌", "Third explicit intent")
    review.once("dialog", lambda dialog: dialog.accept())
    review.get_by_role("button", name="Delete", exact=True).click()
    expect(review.get_by_role("button", name="Save verdict", exact=True)).to_be_visible()
    _note(review)
    device = support.indexeddb(review)
    mutations = sorted(device["mutations"], key=lambda row: row["sequence"])
    verdicts = [row for row in mutations if row["field"] == "verdict"]
    assert [(row["operation"], row["value"]) for row in verdicts] == [
        ("set", "should_read"), ("set", "could_read"), ("set", "dont_read"), ("delete", None)]
    assert len(mutations) == len({row["mutation_id"] for row in mutations}) == 5
    assert len({row["base_revision"] for row in verdicts}) == 1
    assert support.db_rows(lab.database, "SELECT * FROM sync_changes ORDER BY revision") == baseline
    assert len(lab.server.receipts) == before_requests
    lab.checkpoint("offline19-cold-library-ordered-device-saves", review)
    lab.close_browser()
    page = lab.open_browser(offline=True)
    page.goto(lab.server.origin + "/paper/" + KEY)
    expect(questions._notes_editor(page)).to_have_value(MANUAL)
    assert sorted(support.indexeddb(page)["mutations"], key=lambda row: row["sequence"]) == mutations
    assert len(lab.server.receipts) == before_requests
    lab.checkpoint("offline19-server-off-browser-restart", page)
    lab.server.start()
    lab.context.set_offline(False)
    receipts = _settled(lab, page)
    assert len(receipts) == 5 and {row["mutation_id"] for row in receipts} == {
        row["mutation_id"] for row in mutations}
    assert repositories.get_label_verdict(lab.database, KEY) is None
    assert repositories.get_review_note(lab.database, KEY) == MANUAL
    changes = support.db_rows(lab.database, "SELECT * FROM sync_changes ORDER BY revision")
    assert len([row for row in changes if row["field"] == "verdict"]) == 4
    pushes = [row for row in lab.server.receipts if row["method"] == "POST" and row["path"] == "/api/sync/push"]
    assert len(pushes) == 1 and len(pushes[0]["body"]["mutations"]) == 5
    result = _push(lab, pushes[0]["body"])
    assert [row["status"] for row in result["results"]] == ["already_applied"] * 5
    assert support.db_rows(lab.database, "SELECT * FROM sync_changes ORDER BY revision") == changes
    assert support.db_rows(lab.database, "SELECT * FROM sync_mutations ORDER BY mutation_id") == receipts
    assert questions._post_count(lab, "/api/library/ask") == 0
    lab.checkpoint("offline19-reconnected-once-and-immutable-replay", page)
    support.write_json(lab.artifact / "offline19-result.json", {
        "device_mutations": mutations, "sync_receipts": receipts, "replay": result,
        "verdict_transitions": 4, "pending_after": support.indexeddb(page)["mutations"],
        "manual_body": MANUAL, "boundary": "owned synthetic Chrome/TCP/SQLite; no physical iOS/remote/model proof"})


@pytest.mark.parametrize("keep_mine", [True, False], ids=["keep-device", "use-server"])
def test_real_sync_conflict_compares_both_sides_and_other_field_survives(
        question_browser, record_property, keep_mine):
    from patchright.sync_api import expect

    lab = question_browser
    page = _ready(lab, record_property)
    page.goto(lab.server.origin + "/paper/" + KEY)
    expect(questions._notes_editor(page)).to_have_value("Initial manual note")
    page.wait_for_load_state("networkidle", timeout=support.ACTION_TIMEOUT_MS)
    lab.context.set_offline(True)
    questions._assert_offline_transport(lab, page)
    _verdict(page, "Could read", "Device independent judgement")
    _note(page)
    device = deepcopy(support.indexeddb(page)["mutations"])
    local_verdict = next(row for row in device if row["field"] == "verdict")
    remote = {"mutation_id": str(uuid4()), "device_id": "owned-second-browser", "item_key": KEY,
              "field": "verdict", "operation": "set", "value": "must_read",
              "comment": "Server independent judgement", "base_revision": local_verdict["base_revision"]}
    assert _push(lab, {"protocol": 1, "mutations": [remote]})["results"][0]["status"] == "applied"
    lab.context.set_offline(False)
    expect(page.get_by_text("Changed elsewhere — compare before resolving", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Server", exact=True)).to_have_value("Must read\n\nComment:\nServer independent judgement")
    expect(page.get_by_role("textbox", name="This device", exact=True)).to_have_value("Could read\n\nComment:\nDevice independent judgement")
    fields = {name: page.get_by_role("textbox", name=name, exact=True).evaluate(
        "el => ({rows:el.rows,clientHeight:el.clientHeight,scrollHeight:el.scrollHeight})")
        for name in ("Server", "This device")}
    support.write_json(lab.artifact / "offline19-conflict-fields.json", fields)
    assert all(row["rows"] == 4 and row["scrollHeight"] <= row["clientHeight"] for row in fields.values())
    assert repositories.get_review_note(lab.database, KEY) == MANUAL
    assert repositories.get_label_verdict(lab.database, KEY)["user_priority"] == "must_read"
    preserved = next(row for row in support.indexeddb(page)["mutations"]
                     if row["mutation_id"] == local_verdict["mutation_id"])
    assert preserved["status"] == "conflict"
    assert all(preserved[key] == value for key, value in local_verdict.items() if key != "status")
    phase = "offline19-conflict-" + ("keep-device" if keep_mine else "use-server")
    lab.checkpoint(phase, page)
    page.get_by_role("button", name="Keep mine" if keep_mine else "Use server", exact=True).click()
    receipts = _settled(lab, page)
    expect(page.get_by_text("Saved on this device.", exact=True)).to_be_visible()
    expect(page.get_by_text("Saved on this device — waiting to sync.", exact=True)).to_have_count(0)
    expect(page.get_by_text("Changed elsewhere — compare before resolving", exact=True)).to_have_count(0)
    verdict = repositories.get_label_verdict(lab.database, KEY)
    assert verdict["user_priority"] == ("could_read" if keep_mine else "must_read")
    assert verdict["comment"] == ("Device independent judgement" if keep_mine else "Server independent judgement")
    assert repositories.get_review_note(lab.database, KEY) == MANUAL
    saved = [json.loads(row["request_json"]) for row in receipts]
    resolution = [row for row in saved if row.get("resolves_mutation_id") == local_verdict["mutation_id"]]
    assert len(resolution) == 1 and resolution[0]["mutation_id"] != local_verdict["mutation_id"]
    assert questions._post_count(lab, "/api/library/ask") == 0
    lab.checkpoint(phase + "-resolved", page)
    support.write_json(lab.artifact / "offline19-result.json", {
        "original_device": device, "server_mutation": remote, "resolution": resolution[0],
        "verdict": verdict, "note": MANUAL, "receipt_count": len(receipts),
        "boundary": "actual API/IDB same-field conflict + different-field merge; synthetic data only"})
