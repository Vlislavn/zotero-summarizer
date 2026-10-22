"""Opt-in real committed push reply loss; author-only Wave62 proposal.

Reuses the existing installed-Chrome/API/native fixture, not its three journeys.
The exact-local route.fetch -> abort shim forwards the original request once,
buffers the actual response, and never fulfills it. This is NOT packet-level loss.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit
from uuid import UUID

from tests import _review_question_browser as support
from tests import test_review_question_browser as journeys
from zotero_summarizer.integrations._zotero_write_tags import _native_note_html
from zotero_summarizer.services.zotero.pending import USER_NOTE_MARKER, build_user_note_html
from zotero_summarizer.storage.review_note_projection import snapshot

# Reuse the decorated fixture and opt-in policy without importing test functions.
question_browser = journeys.question_browser
pytestmark = journeys.pytestmark
KEY = support.LIBRARY_KEY
MANUAL = "Initial manual note"
QUESTION = "Offline question whose committed reply must be lost <one> & 😀"


def _bindings(lab, request, record_property):
    path = Path(__file__).resolve()
    assert path == support.ROOT / "tests/test_review_question_lost_response_browser.py"
    names = (__name__, support.__name__, journeys.__name__,
             "zotero_summarizer.api.app", "zotero_summarizer.api.local_access",
             "zotero_summarizer.api.routes.sync", "zotero_summarizer.services.sync.service",
             "zotero_summarizer.storage._repo_sync", "zotero_summarizer.storage.question_sync",
             "zotero_summarizer.storage.review_questions", "zotero_summarizer.storage.review_note_projection",
             "zotero_summarizer.services.golden.verdict_effects", "zotero_summarizer.services.zotero.pending",
             "zotero_summarizer.integrations._zotero_write_tags")
    bindings = {}
    for name in names:
        source = Path(sys.modules[name].__file__).resolve()
        assert source.is_relative_to(support.ROOT), (name, source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        bindings[name] = {"path": str(source), "sha256": digest}
        record_property(name, f"{source}:{digest}")
    library = {}
    for name in ("patchright.sync_api._generated", "patchright._impl._network", "patchright._impl._fetch"):
        source = Path(sys.modules[name].__file__).resolve()
        library[name] = {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    receipt = {"case": request.node.nodeid, "modules": bindings, "installed_transport_library": library,
               "fault_boundary": "real route.fetch response buffered, then offline + route.abort; not packet-level"}
    support.write_json(lab.artifact / "lost-response-source-bindings.json", receipt)
    record_property("question_browser_artifact", str(lab.artifact))


def _database(lab):
    return {table: support.db_rows(lab.database, f"SELECT * FROM {table} ORDER BY 1")
            for table in support.TABLES}


def _native(lab, count, body):
    note_id = journeys._assert_native_projection(lab, count)
    rows = support.db_rows(lab.settings.zotero_data_dir / "zotero.sqlite",
        "SELECT i.key,n.itemID,n.parentItemID,n.note,n.title FROM itemNotes n "
        "JOIN items i ON i.itemID=n.itemID ORDER BY n.itemID")
    owned = [row for row in rows if USER_NOTE_MARKER in row["note"]]
    assert len(owned) == 1 and owned[0]["itemID"] == note_id
    assert owned[0]["note"] == _native_note_html(build_user_note_html(body))
    with closing(sqlite3.connect(lab.database)) as conn:
        desired = snapshot(conn, KEY)
    assert desired["body"] == body and desired["target_key"] == KEY
    assert desired["pending"] is False
    return {"notes": rows, "owned": owned[0], "projection": desired}


def _meta(device):
    return {row["key"]: row["value"] for row in device["meta"]}


def _ack_key(mutation):
    return "ack:" + json.dumps([mutation["device_id"], mutation["item_key"], mutation["field"]],
                               separators=(",", ":"))


def _pending(device, mutation, cursor):
    assert device["online"] is False
    assert device["mutations"] == [mutation]
    meta = _meta(device)
    assert meta["cursor"] == cursor
    assert meta["question:" + mutation["mutation_id"]] == mutation["value"]
    assert _ack_key(mutation) not in meta, "Lost reply was acknowledged on device"


def _settled(page, cursor, canonical=None, mutation=None):
    device = support.indexeddb(page)
    meta = _meta(device)
    paper = next((row for row in device["papers"] if row["item_key"] == KEY), None)
    detail = meta.get("response:review:" + KEY)
    expected = [] if canonical is None else [canonical]
    if (not device["online"] or device["mutations"] or meta.get("cursor") != cursor
            or not paper or not detail or paper.get("questions") != expected
            or detail.get("questions") != expected or detail.get("user_note") != MANUAL
            or paper.get("review_note") != MANUAL):
        return False
    if canonical is None:
        return paper["revisions"]["review_question"] == 0
    return (paper["revisions"]["review_question"] == cursor
            and meta.get(_ack_key(mutation)) == {
                "mutation_id": mutation["mutation_id"], "applied_revision": cursor})


def _committed(lab, mutation, baseline):
    state = _database(lab)
    assert len(state["review_questions"]) == len(state["sync_mutations"]) == 1
    question = state["review_questions"][0]
    assert {key: question[key] for key in mutation["value"]} == mutation["value"]
    assert question["submitted_at"]
    assert state["review_notes"] == baseline["database"]["review_notes"]
    assert state["label_verdicts"] == baseline["database"]["label_verdicts"] == []
    changes = state["sync_changes"]
    assert len(changes) == len(baseline["database"]["sync_changes"]) + 1
    assert changes[:-1] == baseline["database"]["sync_changes"]
    transition = changes[-1]
    assert (transition["item_key"], transition["field"], transition["value"]) == (
        KEY, "review_question", mutation["mutation_id"])
    revision = transition["revision"]
    assert revision > baseline["cursor"]
    receipt = state["sync_mutations"][0]
    assert (receipt["device_id"], receipt["item_key"], receipt["field"], receipt["operation"],
            receipt["mutation_id"]) == (mutation["device_id"], KEY, "review_question", "create",
                                        mutation["mutation_id"])
    immutable, result = json.loads(receipt["request_json"]), json.loads(receipt["result_json"])
    for key in ("mutation_id", "device_id", "item_key", "field", "operation", "value",
                "base_revision", "comment", "model_priority"):
        assert immutable[key] == mutation[key]
    assert immutable["resolves_mutation_id"] is None
    assert datetime.fromisoformat(immutable["created_at"]) == datetime.fromisoformat(mutation["created_at"])
    assert result["status"] == "applied" and result["applied_revision"] == revision
    assert result["canonical"] == {"value": question} and "question_mirror" not in result
    native = _native(lab, 1, MANUAL + "\n\nSaved questions:\n1. " + QUESTION)
    assert native["projection"]["revision"] == revision
    assert len(state["review_note_mirror_intents"]) == 1
    intent = state["review_note_mirror_intents"][0]
    assert (intent["item_key"], intent["revision"], intent["body"], intent["target_key"], intent["acknowledged"]) == (
        KEY, revision, native["projection"]["body"], KEY, 1)
    for key in ("key", "itemID", "parentItemID", "title"):
        assert native["owned"][key] == baseline["native"]["owned"][key]
    assert [row for row in native["notes"] if USER_NOTE_MARKER not in row["note"]] == [
        row for row in baseline["native"]["notes"] if USER_NOTE_MARKER not in row["note"]]
    assert journeys._post_count(lab, "/api/library/ask") == baseline["qa_count"] == 0
    assert journeys._post_count(lab, "/api/library/questions") == 0
    return {"database": state, "native": native, "revision": revision, "canonical": question,
            "immutable_request": immutable, "immutable_result": result}


def _drop_reply(lab, page, mutation, baseline, fault):
    def drop(route):
        request = route.request
        if request.method != "POST":
            route.fallback()  # The unchanged fixture's local-route guard still owns other traffic.
            return
        assert request.url == lab.server.origin + "/api/sync/push"
        assert urlsplit(request.url).netloc == urlsplit(lab.server.origin).netloc
        assert "request_body" not in fault, "More than one intercepted push"
        reply = None
        fault.update(stage="original-browser-request-stalled", request_body=json.loads(request.post_data),
                     request_post_data=request.post_data, request_url=request.url, request_headers=request.all_headers(),
                     transport="transparent route.fetch + abort shim; not packet-level TCP loss")
        support.write_json(lab.artifact / "fault-stage.json", fault)
        try:
            assert fault["request_body"] == {"protocol": 1, "mutations": [mutation], "predecessors": []}
            # No URL/method/header/body override; no redirect or transport retry.
            reply = route.fetch(max_redirects=0, max_retries=0, timeout=support.ACTION_TIMEOUT_MS)
            fault.update(stage="real-api-response-buffered-before-browser-ack", http_status=reply.status,
                         actual_response=reply.json())
            support.write_json(lab.artifact / "fault-stage.json", fault)
            assert reply.status == 200 and reply.url == request.url
            actual = fault["actual_response"]
            assert actual["protocol"] == 1 and len(actual["results"]) == 1
            committed = _committed(lab, mutation, baseline)
            result = actual["results"][0]
            assert result == {**committed["immutable_result"], "question_mirror": {
                "note_written": True, "note_error": None, "note_pending": False,
                "projection_revision": committed["revision"], "target_key": KEY}}
            receipts = [row for row in lab.server.receipts
                        if row["method"] == "POST" and row["path"] == "/api/sync/push"]
            assert len(receipts) == 1 and receipts[0]["status"] == 200
            assert receipts[0]["body"] == fault["request_body"]
            assert request.failure is None, "Browser failed before the intended fault"
            assert not [row for row in lab.responses if row["url"] == request.url]
            before_ack = support.indexeddb(page)
            assert before_ack["online"] is True and before_ack["mutations"] == [mutation]
            assert _meta(before_ack)["cursor"] == baseline["cursor"]
            assert _ack_key(mutation) not in _meta(before_ack)
            fault.update(stage="commit-receipt-native-verified-before-fault", committed=committed,
                         actual_server_receipt=receipts[0], indexeddb_before_fault=before_ack,
                         qa_count_before_fault=journeys._post_count(lab, "/api/library/ask"))
            support.write_json(lab.artifact / "commit-before-drop.json", fault)
        except Exception as error:
            fault["setup_or_assertion_error"] = repr(error)
            raise
        finally:
            # While the original browser request is still stalled, close the retry window.
            lab.context.set_offline(True)
            route.abort("connectionreset")  # Never fulfill or synthesize an HTTP reply.
            if reply is not None:
                reply.dispose()
            fault["offline_before_abort"] = True
            fault["abort_code"] = "connectionreset"
            fault["stage"] = "reply-aborted-context-offline" if "committed" in fault else "fault-setup-failed"
            support.write_json(lab.artifact / "fault-stage.json", fault)
    return drop


def test_lost_postcommit_push_reply_replays_once_after_offline_browser_api_restart(
        question_browser, request, record_property):
    from patchright.sync_api import expect

    lab = question_browser
    _bindings(lab, request, record_property)
    page = lab.open_browser()
    page.goto(lab.server.origin + "/paper/" + KEY)
    expect(page.get_by_role("textbox", name="Question about this paper")).to_be_visible()
    expect(journeys._notes_editor(page)).to_have_value(MANUAL)
    # Deliver the fixture's unchanged manual body through the actual guarded API.
    saved = journeys._http(page, "/api/golden/review-note", "POST", {"item_key": KEY, "note": MANUAL})
    assert saved["status"] == 200 and saved["body"]["note_written"] is True
    baseline = {"database": _database(lab), "native": _native(lab, 0, MANUAL),
                "qa_count": journeys._post_count(lab, "/api/library/ask")}
    baseline["cursor"] = baseline["database"]["sync_changes"][-1]["revision"]
    assert baseline["database"]["review_questions"] == baseline["database"]["sync_mutations"] == []
    assert baseline["qa_count"] == journeys._post_count(lab, "/api/sync/push") == 0
    support.wait_for(lambda: _settled(page, baseline["cursor"]))
    assert support.main_world(page, "() => navigator.serviceWorker.ready.then(() => true)") is True
    assert support.main_world(page, "() => Boolean(navigator.serviceWorker.controller)") is True
    page.wait_for_function("""async () => {
      const cache = await caches.open('zs-app-shell-v1');
      return (await cache.keys()).some(row => row.url.includes('/assets/'));
    }""")
    support.write_json(lab.artifact / "baseline.json", baseline)
    lab.context.set_offline(True)
    journeys._assert_offline_transport(lab, page)
    journeys._submit(page, QUESTION)
    expect(page.get_by_text(
        "Saved on this device — server comment pending.", exact=True)).to_be_visible()
    support.wait_for(lambda: len(support.indexeddb(page)["mutations"]) == 1)
    device = support.indexeddb(page)
    mutation = device["mutations"][0]
    assert mutation["mutation_id"] == str(UUID(mutation["mutation_id"])) == mutation["value"]["submission_id"]
    assert mutation["value"] == {"submission_id": mutation["mutation_id"], "item_key": KEY,
                                 "question": QUESTION, "origin": "typed", "preset_id": None}
    assert (mutation["item_key"], mutation["field"], mutation["operation"], mutation["status"],
            mutation["base_revision"]) == (KEY, "review_question", "create", "pending", 0)
    _pending(device, mutation, baseline["cursor"])
    assert _database(lab) == baseline["database"]
    lab.checkpoint("offline-queued-one-question", page)
    support.write_json(lab.artifact / "offline-mutation.json", mutation)

    fault = {}
    push_url = lab.server.origin + "/api/sync/push"
    lab.context.route(push_url, _drop_reply(lab, page, mutation, baseline, fault))
    with page.expect_event("requestfailed", predicate=lambda row: row.url == push_url) as lost:
        lab.context.set_offline(False)
    # Pump the dispatcher until the route callback also finishes its abort/disposal.
    support.wait_for(lambda: page.evaluate("() => true") and fault.get("stage") in (
        "reply-aborted-context-offline", "fault-setup-failed"))
    assert fault["stage"] == "reply-aborted-context-offline" and "setup_or_assertion_error" not in fault
    fault["browser_request_failure"] = lost.value.failure
    assert fault["browser_request_failure"]
    support.write_json(lab.artifact / "fault-stage.json", fault)
    journeys._assert_offline_transport(lab, page)
    _pending(support.indexeddb(page), mutation, baseline["cursor"])
    committed = fault["committed"]
    assert _committed(lab, mutation, baseline) == committed
    assert journeys._post_count(lab, "/api/sync/push") == 1
    lab.checkpoint("committed-reply-lost-device-pending", page)
    lab.close_browser()
    lab.server.stop()
    restart_receipts = len(lab.server.receipts)
    page = lab.open_browser(offline=True)
    journeys._assert_offline_transport(lab, page)
    page.goto(lab.server.origin + "/paper/" + KEY)
    journeys._assert_offline_transport(lab, page)
    expect(journeys._saved_questions(page).get_by_text(QUESTION, exact=True)).to_be_visible()
    expect(journeys._saved_questions(page).locator("li")).to_have_count(1)
    expect(journeys._notes_editor(page)).to_have_value(MANUAL)
    _pending(support.indexeddb(page), mutation, baseline["cursor"])
    assert len(lab.server.receipts) == restart_receipts, "Offline restart silently reached API"
    assert _committed(lab, mutation, baseline) == committed
    assert journeys._post_count(lab, "/api/sync/push") == 1
    lab.checkpoint("offline-browser-api-restart-same-pending-uuid", page)
    lab.server.start()  # Restart the actual lifespan only after the offline read.

    with page.expect_response(lambda response: response.url == push_url) as replayed:
        lab.context.set_offline(False)
    replay = replayed.value.json()
    support.write_json(lab.artifact / "actual-replay-response.json", replay)
    assert replayed.value.status == 200 and replay["protocol"] == 1 and len(replay["results"]) == 1
    assert replay["results"][0] == {**committed["immutable_result"], "status": "already_applied",
        "question_mirror": {"note_written": False, "note_error": None, "note_pending": False,
                            "projection_revision": committed["revision"], "target_key": KEY}}
    support.wait_for(lambda: _settled(page, committed["revision"], committed["canonical"], mutation))
    status = journeys._http(page, "/api/sync/status")
    assert status["status"] == 200 and status["body"]["cursor"] == committed["revision"]
    assert status["body"]["mutations"] == 1 and status["body"]["conflicts"] == 0
    pulls = [row for row in lab.responses if row["url"] == (
        lab.server.origin + f'/api/sync/pull?protocol=1&since={baseline["cursor"]}')]
    assert pulls and pulls[-1]["status"] == 200 and pulls[-1]["body"]["cursor"] == committed["revision"]
    paper = next(row for row in pulls[-1]["body"]["papers"] if row["item_key"] == KEY)
    assert paper["questions"] == [committed["canonical"]] and paper["review_note"] == MANUAL
    assert paper["revisions"]["review_question"] == committed["revision"]
    assert _committed(lab, mutation, baseline) == committed
    pushes = [row for row in lab.server.receipts if row["method"] == "POST" and row["path"] == "/api/sync/push"]
    assert len(pushes) == 2 and all(row["status"] == 200 and row["body"] == fault["request_body"] for row in pushes)
    assert len([row for row in lab.responses if row["url"] == push_url]) == 1
    expect(journeys._saved_questions(page).locator("li")).to_have_count(1)
    expect(journeys._saved_questions(page).get_by_text(QUESTION, exact=True)).to_be_visible()
    expect(journeys._notes_editor(page)).to_have_value(MANUAL)
    lab.checkpoint("settled-already-applied-no-qa", page)
    support.write_json(lab.artifact / "lost-response-result.json", {
        "status": "passed", "fault_boundary": fault["transport"], "mutation": mutation,
        "tuple": [mutation["device_id"], KEY, "review_question"], "revision": committed["revision"],
        "questions": 1, "question_transitions": 1, "sync_receipts": 1, "actual_pushes": pushes,
        "qa_before": baseline["qa_count"], "qa_after": journeys._post_count(lab, "/api/library/ask"),
        "native_note_identity": {key: committed["native"]["owned"][key]
                                 for key in ("key", "itemID", "parentItemID", "title")},
        "manual_body_unchanged": MANUAL, "settled_cursor": status["body"]["cursor"],
        "indexeddb": support.indexeddb(page), "native": committed["native"],
        "cleanup_receipt": "cleanup.json written by unchanged synthetic_lab teardown",
        "boundary": "owned synthetic SQLite/PDF; no provider calls, private Zotero, Add or mobile proof"})
    print("QUESTION_BROWSER_ARTIFACT", lab.artifact)
