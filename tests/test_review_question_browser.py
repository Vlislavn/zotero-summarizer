"""Opt-in real installed Chrome → actual API/sync/native synthetic Zotero acceptance.

Requires a serialized slot, built frontend, and explicit owned synthetic fixture.
No endpoints/QA answers are replaced; deterministic counts and disabled-AI errors
are not scientific or live-Zotero acceptance. All artifacts stay under data/.
"""
from contextlib import closing
import json
import os
import sqlite3
from urllib.parse import quote
from uuid import uuid4

import pytest

from tests._review_question_browser import (
    LIBRARY_KEY, PAGE_COUNT_QUESTION, db_rows, indexeddb, main_world, query_state,
    synthetic_lab, write_json, order_question_requests,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_REVIEW_QUESTION_BROWSER_SMOKE") != "1",
    reason="opt-in installed-browser synthetic acceptance",
)


@pytest.fixture
def question_browser(monkeypatch):
    assert os.environ.get("ZS_REVIEW_QUESTION_BROWSER_SYNTHETIC") == "1", "Explicit synthetic fixture required"
    assert os.environ.get("ZS_REVIEW_QUESTION_BROWSER_SLOT") == "reserved", "Parent serialized slot required"
    yield from synthetic_lab(monkeypatch)


def _http(page, path, method="GET", body=None):
    return page.evaluate("""async ({path, method, body}) => {
      const res = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
        ...(body === null ? {} : {body: JSON.stringify(body)})});
      return {status: res.status, body: await res.json()};
    }""", {"path": path, "method": method, "body": body})


def _questions(lab):
    return db_rows(lab.database, "SELECT * FROM review_questions ORDER BY submitted_at,submission_id")


def _saved_questions(page):
    return page.get_by_role("list", name="Saved review questions")


def _notes_editor(page):
    return page.get_by_role("textbox", name="Manual notes", exact=True)


def _submit(page, text=PAGE_COUNT_QUESTION):
    page.get_by_role("textbox", name="Question about this paper").fill(text)
    page.get_by_role("button", name="Ask", exact=True).click()


def _post_count(lab, path):
    return sum(row["method"] == "POST" and row["path"] == path for row in lab.server.receipts)


def _assert_offline_transport(lab, page):
    requests = len(lab.server.receipts)
    assert page.evaluate("navigator.onLine") is False
    assert page.evaluate("""async origin => {
      try { await fetch(origin + '/api/sync/status?uncached_offline_probe=1', {cache:'no-store'}); return false; }
      catch { return true; }
    }""", lab.server.origin) is True
    assert len(lab.server.receipts) == requests, "Genuine offline probe reached actual API"
    return requests


def _assert_native_projection(lab, expected_count):
    from zotero_summarizer.services.zotero.pending import USER_NOTE_MARKER
    from zotero_summarizer.storage.review_note_projection import snapshot

    rows = db_rows(lab.settings.zotero_data_dir / "zotero.sqlite", "SELECT itemID,note FROM itemNotes")
    owned = [row for row in rows if USER_NOTE_MARKER in row["note"]]
    assert len(owned) == 1
    assert owned[0]["note"].startswith('<div class="zotero-note znv1">')
    assert sum(row["note"].count("Unrelated native note") for row in rows) == 1
    with closing(sqlite3.connect(lab.database)) as conn:
        desired = snapshot(conn, LIBRARY_KEY)
    assert desired["pending"] is False and desired["target_key"] == LIBRARY_KEY
    assert len(_questions(lab)) == expected_count
    assert list(lab.settings.zotero_data_dir.glob("*.backup.*")) or list(
        lab.settings.zotero_data_dir.glob("zotero.sqlite.backup*"))
    return owned[0]["itemID"]


def test_today_library_questions_offline_restart_and_real_sync(question_browser):
    from patchright.sync_api import expect

    lab = question_browser
    page = lab.open_browser()
    page.goto(lab.server.origin + "/today")
    expect(page.get_by_role("heading", name="Today’s reading", exact=True)).to_be_visible()
    expect(page.get_by_text("Synthetic Today paper", exact=True)).to_be_visible()
    page.get_by_role("link", name="Open full review ↗", exact=True).click()
    expect(page.get_by_role("heading", name="Synthetic Today paper", exact=True)).to_be_visible()
    # has_pdf=False; actual completed cached render enables the unchanged rail Ask gate.
    expect(page.get_by_role("button", name="Is there external validation?", exact=True)).to_be_visible()
    draft = "Dirty manual draft <must survive>"
    _notes_editor(page).fill(draft)
    save_style = page.get_by_role("button", name="Save", exact=True).evaluate("""el => {
      const reference = document.createElement('span');
      reference.className = 'text-white'; document.body.append(reference);
      const expected = getComputedStyle(reference).color; reference.remove();
      return {actual: getComputedStyle(el).color, expected,
        background: getComputedStyle(el).backgroundColor, disabled: el.disabled};
    }""")
    write_json(lab.artifact / "active-save-foreground.json", save_style)
    lab.checkpoint("active-save-foreground", page)
    assert save_style["disabled"] is False
    assert save_style["actual"] == save_style["expected"], save_style
    with page.expect_response(lambda response: response.url.endswith("/api/library/ask")) as answered:
        page.get_by_role("button", name="Is there external validation?", exact=True).click()
    assert answered.value.status == 409
    failure = answered.value.json()
    assert failure["error"] == "llm_disabled" and failure["details"]["question_saved"] is True
    preset = failure["details"]["question_submission"]
    assert preset["origin"] == "preset" and preset["preset_id"] == "external_validation"
    assert preset["item_key"] == lab.stable
    expect(_saved_questions(page).get_by_text(preset["question"], exact=True)).to_be_visible()
    expect(_notes_editor(page)).to_have_value(draft)
    expect(page.get_by_role("alert").filter(has_text="Ask failed")).to_be_visible()
    original = _questions(lab)
    page.get_by_role("button", name="Retry answer", exact=True).click()
    expect(page.get_by_role("button", name="Retry answer", exact=True)).to_be_enabled()
    assert _questions(lab) == original
    retry_bodies = [row["body"] for row in lab.server.receipts if row["path"] == "/api/library/ask"]
    assert len(retry_bodies) == 2 and retry_bodies[0] == retry_bodies[1]
    lab.checkpoint("today-preset-error-retry", page)

    page.goto(lab.server.origin + "/library")
    page.get_by_role("button").filter(has_text="Synthetic library paper").first.click()
    expect(_notes_editor(page)).to_have_value("Initial manual note")
    _notes_editor(page).fill(draft)
    page.get_by_text("Ask the paper", exact=True).click()
    with page.expect_response(lambda response: response.url.endswith("/api/library/ask")) as counted:
        _submit(page)
    result = counted.value.json()
    assert counted.value.status == 200 and result["answer"] == "1 pages"
    assert result["model"] == "deterministic-metadata" and result["question_saved"] is True
    typed = result["question_submission"]
    assert typed["origin"] == "typed" and typed["item_key"] == LIBRARY_KEY
    expect(_saved_questions(page).get_by_text(PAGE_COUNT_QUESTION, exact=True)).to_be_visible()
    expect(_notes_editor(page)).to_have_value(draft)
    before = _questions(lab)
    assert _http(page, "/api/library/questions", "POST", {key: typed[key] for key in
                  ("submission_id", "item_key", "question", "origin", "preset_id")})["status"] == 200
    assert _questions(lab) == before
    conflict = _http(page, "/api/library/questions", "POST", {**typed, "question": "Changed same UUID"})
    assert conflict["status"] == 409 and conflict["body"]["error"] == "submission_conflict"
    assert _questions(lab) == before
    with page.expect_response(lambda response: response.url.endswith("/api/library/ask")) as repeated:
        _submit(page)
    repeat = repeated.value.json()["question_submission"]
    assert repeat["submission_id"] != typed["submission_id"]
    expect(_saved_questions(page).get_by_text(PAGE_COUNT_QUESTION, exact=True)).to_have_count(2)
    expect(_notes_editor(page)).to_have_value(draft)
    note_id = _assert_native_projection(lab, 3)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved to the app and Zotero.", exact=True)).to_be_visible()
    assert db_rows(lab.database, "SELECT note FROM review_notes WHERE item_key=?", (LIBRARY_KEY,)) == [{"note": draft}]
    _notes_editor(page).fill("")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved to the app and Zotero.", exact=True)).to_be_visible()
    assert db_rows(lab.database, "SELECT note FROM review_notes WHERE item_key=?", (LIBRARY_KEY,)) == [{"note": ""}]
    assert _assert_native_projection(lab, 3) == note_id
    lab.checkpoint("library-repeat-manual-clear", page)

    # Synthetic materialization association only, not an explicit Add/review acceptance claim.
    with sqlite3.connect(lab.database) as conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key=? WHERE stable_feed_key=?",
                     (LIBRARY_KEY, lab.stable))
    union = _http(page, "/api/library/questions?item_key=" + quote(LIBRARY_KEY))["body"]["questions"]
    assert {row["submission_id"] for row in union} == {row["submission_id"] for row in _questions(lab)}
    assert next(row for row in union if row["submission_id"] == preset["submission_id"])["item_key"] == lab.stable
    assert _http(page, "/api/library/questions", "POST", preset)["status"] == 200
    assert _assert_native_projection(lab, 3) == note_id

    page.goto(lab.server.origin + "/paper/" + LIBRARY_KEY)
    expect(_saved_questions(page).locator("li")).to_have_count(3)
    expect(page.get_by_text("Q: " + PAGE_COUNT_QUESTION, exact=True)).to_have_count(0)
    # Patchright defaults to an isolated realm; observe service workers in the actual page realm.
    assert main_world(page, "() => navigator.serviceWorker.ready.then(() => true)") is True
    assert main_world(page, "() => Boolean(navigator.serviceWorker.controller)") is True
    page.wait_for_function("""async () => {
      const cache = await caches.open('zs-app-shell-v1');
      const keys = await cache.keys(); return keys.some(row => row.url.includes('/assets/'));
    }""")
    page.wait_for_function("""async () => {
      const req = indexedDB.open('zotero-summarizer-offline', 1);
      const db = await new Promise(resolve => req.onsuccess = () => resolve(req.result));
      const rows = await new Promise(resolve => { const r = db.transaction('mutations').objectStore('mutations').getAll();
        r.onsuccess = () => resolve(r.result); }); db.close(); return rows.length === 0;
    }""")
    baseline_asks = _post_count(lab, "/api/library/ask")
    lab.context.set_offline(True)
    _assert_offline_transport(lab, page)
    offline_text = "Offline original question <durable>"
    _submit(page, offline_text)
    expect(page.get_by_text("Saved on this device — server comment pending.", exact=True)).to_be_visible()
    device = indexeddb(page)
    lab.checkpoint("genuine-offline-device-save", page)
    same_page_visible, same_page_failure = True, None
    try:
        expect(_saved_questions(page).get_by_text(offline_text, exact=True)).to_be_visible()
    except AssertionError as failure:
        same_page_visible, same_page_failure = False, str(failure)
    lab.checkpoint("offline-same-page-observed", page)  # Before any offline CDP probe.
    request_count = _assert_offline_transport(lab, page)
    diagnosis = query_state(page, LIBRARY_KEY, probe_local=True)
    _assert_offline_transport(lab, page)
    assert diagnosis is not None, "Actual React provider QueryClient not located"
    assert diagnosis["directLocalRead"]["item_key"] == LIBRARY_KEY
    assert any(row["question"] == offline_text for row in diagnosis["directLocalRead"]["questions"])
    assert len(lab.server.receipts) == request_count, "Offline local-read probe reached API"
    write_json(lab.artifact / "offline-display-diagnosis.json", {
        "same_page_visible": same_page_visible, "failure": same_page_failure,
        "query": diagnosis, "indexeddb": indexeddb(page), "original_device_save": device,
        "api_request_count_unchanged": request_count,
    })
    assert device["online"] is False and len(device["mutations"]) == 1
    mutation = device["mutations"][0]
    assert mutation["field"] == "review_question" and mutation["operation"] == "create"
    assert mutation["mutation_id"] == mutation["value"]["submission_id"]
    assert mutation["value"]["question"] == offline_text and mutation["status"] == "pending"
    assert len(_questions(lab)) == 3 and _post_count(lab, "/api/library/ask") == baseline_asks
    lab.close_browser()
    lab.server.stop()
    lab.server.start()  # Real API lifespan/context restart; same owned port and SQLite stores.
    restart_requests = len(lab.server.receipts)
    page = lab.open_browser(offline=True)
    _assert_offline_transport(lab, page)
    assert len(lab.server.receipts) == restart_requests, "Unexpected pre-navigation reconnect"
    page.goto(lab.server.origin + "/paper/" + LIBRARY_KEY)
    _assert_offline_transport(lab, page)
    assert len(lab.server.receipts) == restart_requests, "Offline restart reached actual API"
    restarted_visible, restarted_failure = True, None
    try:
        expect(_saved_questions(page).get_by_text(offline_text, exact=True)).to_be_visible()
    except AssertionError as failure:
        restarted_visible, restarted_failure = False, str(failure)
    lab.checkpoint("offline-browser-api-restart", page)  # Reopened page before any CDP probe.
    _assert_offline_transport(lab, page)
    assert main_world(page, "() => Boolean(navigator.serviceWorker.controller)") is True
    _assert_offline_transport(lab, page)
    restarted_query = query_state(page, LIBRARY_KEY)
    _assert_offline_transport(lab, page)
    assert len(lab.server.receipts) == restart_requests, "Offline main-world probes delivered HTTP"
    write_json(lab.artifact / "offline-restart-diagnosis.json", {
        "restart_visible": restarted_visible, "failure": restarted_failure,
        "query": restarted_query, "indexeddb": indexeddb(page), "api_request_count_unchanged": restart_requests,
    })
    assert page.evaluate("navigator.onLine") is False
    assert indexeddb(page)["mutations"] == device["mutations"]
    assert len(_questions(lab)) == 3 and _post_count(lab, "/api/library/ask") == baseline_asks
    with page.expect_response(lambda response: response.url == lab.server.origin + "/api/sync/push") as synced:
        lab.context.set_offline(False)
    reconnect = synced.value.json()
    assert synced.value.status == 200 and reconnect["protocol"] == 1
    question_result = next(row for row in reconnect["results"] if row["mutation_id"] == mutation["mutation_id"])
    assert question_result["status"] == "applied" and question_result["question_mirror"]["note_pending"] is False
    expect(_saved_questions(page).locator("li")).to_have_count(4)
    page.wait_for_function("""async id => {
      const req = indexedDB.open('zotero-summarizer-offline', 1);
      const db = await new Promise(resolve => req.onsuccess = () => resolve(req.result));
      const row = await new Promise(resolve => { const r = db.transaction('mutations').objectStore('mutations').get(id);
        r.onsuccess = () => resolve(r.result); }); db.close(); return !row;
    }""", arg=mutation["mutation_id"])
    rows = _questions(lab)
    assert len(rows) == 4
    saved = next(row for row in rows if row["submission_id"] == mutation["mutation_id"])
    assert {key: saved[key] for key in mutation["value"]} == mutation["value"]
    assert _post_count(lab, "/api/library/ask") == baseline_asks
    receipt = db_rows(lab.database, "SELECT request_json,result_json FROM sync_mutations WHERE mutation_id=?",
                      (mutation["mutation_id"],))[0]
    immutable = json.loads(receipt["request_json"])
    assert immutable["value"] == mutation["value"] and immutable["item_key"] == LIBRARY_KEY
    assert json.loads(receipt["result_json"])["status"] == "applied"
    assert _assert_native_projection(lab, 4) == note_id
    assert db_rows(lab.database, "SELECT * FROM label_verdicts") == []
    lab.checkpoint("reconnected-comment-only-sync", page)
    write_json(lab.artifact / "result.json", {"status": "passed" if same_page_visible and restarted_visible else "failed",
               "questions": 4, "offline_mutation": mutation, "same_page_visible": same_page_visible,
               "restart_visible": restarted_visible, "qa_requests_before_reconnect": baseline_asks,
               "qa_requests_after_reconnect": baseline_asks, "same_native_note_id": note_id,
               "boundary": "synthetic source/storage; deterministic QA only"})
    print("QUESTION_BROWSER_ARTIFACT", lab.artifact)
    assert same_page_visible and restarted_visible, "Genuine offline My notes visibility failed; retained exact diagnosis"


def test_acknowledged_retry_preserves_the_composite_delivery_receipt(question_browser):
    from patchright.sync_api import expect

    lab = question_browser
    page = lab.open_browser()
    ordering = order_question_requests(lab, page, first="direct")
    page.goto(lab.server.origin + "/paper/" + LIBRARY_KEY)
    expect(page.get_by_role("textbox", name="Question about this paper")).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith("/api/library/questions")) as saved:
        _submit(page, "Is there external validation?")
    first = saved.value.json()["question_mirror"]
    assert first["note_written"] is True and first["note_pending"] is False
    assert first["note_error"] is None and first["target_key"] == LIBRARY_KEY
    caption = f'Zotero note delivery: acknowledged (revision {first["projection_revision"]}).'
    expect(page.get_by_text(caption, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Retry answer", exact=True)).to_be_enabled()
    original = _questions(lab)
    native_note = _assert_native_projection(lab, 1)
    with page.expect_response(lambda response: response.url.endswith("/api/library/questions")) as retried:
        page.get_by_role("button", name="Retry answer", exact=True).click()
    second = retried.value.json()["question_mirror"]
    assert second == {**first, "note_written": False}
    expect(page.get_by_text(caption, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Retry answer", exact=True)).to_be_enabled()
    assert _questions(lab) == original
    assert _assert_native_projection(lab, 1) == native_note
    assert _post_count(lab, "/api/library/ask") == 2
    _notes_editor(page).fill("A newer manual note after Ask")
    with page.expect_response(lambda response: response.url.endswith("/api/golden/review-note")) as changed:
        page.get_by_role("button", name="Save", exact=True).click()
    current = changed.value.json()
    assert current["note_written"] is True and current["note_pending"] is False
    assert current["projection_revision"] > first["projection_revision"]
    current_caption = f'Zotero note delivery: acknowledged (revision {current["projection_revision"]}).'
    expect(page.get_by_text(current_caption, exact=True)).to_be_visible()
    expect(page.get_by_text(caption, exact=False)).to_have_count(0)
    assert _assert_native_projection(lab, 1) == native_note
    assert _post_count(lab, "/api/library/ask") == 2
    assert _questions(lab) == original
    lab.checkpoint("acknowledged-no-op-retry-and-new-manual-projection", page)
    write_json(lab.artifact / "delivery-result.json", {
        "first_mirror": first, "retry_mirror": second, "manual_mirror": current,
        "visible_caption": current_caption, "obsolete_caption_absent": True,
        "same_submission_rows": True, "same_native_note_id": native_note,
        "actual_request_order": ordering,
        "boundary": "installed Chrome/actual API/owned synthetic Zotero; AI disabled",
    })
    print("QUESTION_BROWSER_ARTIFACT", lab.artifact)


def test_sync_first_receipt_is_acknowledged_without_another_native_write(question_browser):
    from patchright.sync_api import expect

    lab = question_browser
    page = lab.open_browser()
    ordering = order_question_requests(lab, page, first="sync")
    page.goto(lab.server.origin + "/paper/" + LIBRARY_KEY)
    expect(page.get_by_role("textbox", name="Question about this paper")).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith("/api/library/questions")) as saved:
        _submit(page, "Is there external validation?")
    result = saved.value.json()
    first = result["question_mirror"]
    submission = result["question_submission"]
    sync = next(row["body"] for row in lab.responses if row["url"].endswith("/api/sync/push")
                and row["body"]["results"][0]["mutation_id"] == submission["submission_id"])
    leader = sync["results"][0]["question_mirror"]
    assert leader["note_written"] is True and leader["note_pending"] is False
    assert leader["note_error"] is None and leader["target_key"] == LIBRARY_KEY
    assert first == {**leader, "note_written": False}
    caption = f'Zotero note delivery: acknowledged (revision {first["projection_revision"]}).'
    expect(page.get_by_text(caption, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Retry answer", exact=True)).to_be_enabled()
    assert ordering["leader_status"] == 200 and ordering["follower_released"] is True
    assert sorted(ordering["held"]) == ["direct", "sync"]
    assert _post_count(lab, "/api/library/ask") == 1
    native_note = _assert_native_projection(lab, 1)
    assert _questions(lab)[0]["submission_id"] == submission["submission_id"]
    lab.checkpoint("sync-first-direct-noop-acknowledged", page)
    write_json(lab.artifact / "sync-first-result.json", {"ordering": ordering,
               "sync_mirror": leader, "direct_mirror": first, "same_native_note_id": native_note,
               "transport": "actual requests continued without fulfillment/body/header substitution"})


def test_local_storage_rejection_and_invalid_input_never_start_http(question_browser):
    from patchright.sync_api import expect

    lab = question_browser
    page = lab.open_browser()
    page.goto(lab.server.origin + "/paper/" + LIBRARY_KEY)
    expect(page.get_by_role("textbox", name="Question about this paper")).to_be_visible()
    before = _questions(lab)
    saves, asks = _post_count(lab, "/api/library/questions"), _post_count(lab, "/api/library/ask")
    _submit(page, "x" * 2001)
    expect(page.get_by_role("alert").filter(has_text="at most 2000 characters")).to_be_visible()
    assert _questions(lab) == before
    assert (_post_count(lab, "/api/library/questions"), _post_count(lab, "/api/library/ask")) == (saves, asks)
    # Explicit test-only storage fault; separate from genuine context.set_offline journey.
    assert page.evaluate("() => { window.__questionRealmProbe = 'isolated'; return window.__questionRealmProbe; }") == "isolated"
    assert main_world(page, "() => window.__questionRealmProbe ?? null") is None
    assert main_world(page, "() => { window.__questionRealmProbe = 'main'; return window.__questionRealmProbe; }") == "main"
    assert page.evaluate("() => window.__questionRealmProbe") == "isolated"
    write_json(lab.artifact / "realm-counterprobe.json", {"isolated_window": "isolated", "main_window_before": None,
               "main_window_after": "main", "isolated_window_after": "isolated"})
    main_world(page, """() => {
      window.restoreTransaction = IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction = function(names, mode, ...rest) {
        if (mode === 'readwrite') throw new DOMException('Synthetic write denied', 'QuotaExceededError');
        return window.restoreTransaction.call(this, names, mode, ...rest);
      };
    }""")
    _submit(page, "Storage-denied question")
    expect(page.get_by_role("alert").filter(has_text="Question not saved on this device")).to_be_visible()
    assert _questions(lab) == before and indexeddb(page)["mutations"] == []
    assert (_post_count(lab, "/api/library/questions"), _post_count(lab, "/api/library/ask")) == (saves, asks)
    expect(page.get_by_text("Question saved in the app — see My notes for Zotero delivery.", exact=True)).to_have_count(0)
    main_world(page, "() => { IDBDatabase.prototype.transaction = window.restoreTransaction; return true; }")
    invalid = {"submission_id": str(uuid4()), "item_key": LIBRARY_KEY, "question": " ", "origin": "typed"}
    assert _http(page, "/api/library/questions", "POST", invalid)["status"] == 422
    assert _questions(lab) == before and _post_count(lab, "/api/library/ask") == asks
    with page.expect_response(lambda response: response.url.endswith("/api/library/ask")) as restored:
        _submit(page)
    assert restored.value.status == 200 and restored.value.json()["answer"] == "1 pages"
    expect(_saved_questions(page).get_by_text(PAGE_COUNT_QUESTION, exact=True)).to_be_visible()
    assert len(_questions(lab)) == 1
    lab.checkpoint("invalid-and-device-write-error", page)
    print("QUESTION_BROWSER_ARTIFACT", lab.artifact)
