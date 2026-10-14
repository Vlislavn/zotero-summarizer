"""Proposed #48 owned explicit-Add proof; preparation did not execute this module.

The parent-admitted, source-specific review is supplied TEST INPUT, not generated
or verified review evidence. Shared BrowserLab/SQLite/PDF/eligibility stay intact.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote
from uuid import UUID

import pytest

from tests import _review_question_browser as helpers
from tests import test_review_question_browser as browser
from zotero_summarizer.integrations import _zotero_write_tags as native_notes
from zotero_summarizer.integrations import pdf_fetch
from zotero_summarizer.services._common import state
from zotero_summarizer.services.library import (
    _review_cache, _review_identity, quality_review, review_eligibility,
    review_materialize,
)
from zotero_summarizer.services.zotero import pending
from zotero_summarizer.settings import offline_requested
from zotero_summarizer.storage import review_note_projection

question_browser = browser.question_browser
pytestmark = browser.pytestmark
CURRENT_ROOT = Path("/Users/vladnikulin/code/workspaces/zotero-summarizer/issues-all-20261008/integration")
MANUAL = "Explicit Add manual body <preserve>"
DIRTY = "Unsaved Today draft <not a submitted question>"
ADD_PATH = "/api/daily/add-to-library"
SOURCE_SENTENCES = (
    "This original synthetic source describes a local storage experiment.",
    "The fixture contains one page and no clinical claims.",
    "Question comments and manual notes are distinct records.",
)


def _source_receipt(lab, request, record_property):
    modules = (browser, helpers, pdf_fetch, quality_review, _review_cache,
               _review_identity, review_eligibility, review_materialize,
               review_note_projection, native_notes)
    receipt = {"case": request.node.nodeid, "modules": {}}
    for path in [Path(__file__), *(Path(module.__file__) for module in modules)]:
        path = path.resolve()
        assert path.is_relative_to(CURRENT_ROOT), path
        receipt["modules"][str(path)] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    record_property("loaded_source", json.dumps(receipt, sort_keys=True))
    helpers.write_json(lab.artifact / "explicit-add-loaded-source.json", receipt)


def _row(lab, row_id=None):
    clause, value = ("stable_feed_key", lab.stable) if row_id is None else ("id", row_id)
    rows = helpers.db_rows(lab.database, f"SELECT * FROM processed_feed_items WHERE {clause}=?", (value,))
    assert len(rows) == 1
    return rows[0]


def _supplied_review_input(lab, record_property):
    import fitz

    original_row = _row(lab)
    assert original_row["materialized_zotero_key"] is None
    assert offline_requested(), "Owned fixture must remain offline"
    assert lab.settings.pdf_cache_dir.resolve().is_relative_to(lab.artifact.resolve())
    pdf = pdf_fetch.fetch_pdf(helpers.FEED_URL, cache_dir=lab.settings.pdf_cache_dir)
    assert pdf is not None, "PREPARATION_BLOCKED: existing owned Today PDF absent; do not repair"
    pdf = pdf.resolve()
    assert pdf.is_relative_to(lab.settings.pdf_cache_dir.resolve())
    assert pdf_fetch.valid_pdf_path(pdf)
    identity = _review_identity.build_review_identity(
        config=state().app_state.config, pdf_path=str(pdf), source_kind="override", focus_prompt="",
    )
    assert identity["source_path"] == str(pdf) and len(identity["source_sha256"]) == 64
    with fitz.open(pdf) as document:
        assert document.page_count == 1, "PREPARATION_BLOCKED: owned fixture source changed"
        text = " ".join(document[0].get_text().split())
    assert all(sentence in text for sentence in SOURCE_SENTENCES), (
        "PREPARATION_BLOCKED: declared fixture sentences absent; do not substitute source"
    )
    raw = {
        "tldr": SOURCE_SENTENCES[0], "methods": SOURCE_SENTENCES[0],
        "key_findings": [SOURCE_SENTENCES[2]], "limitations": SOURCE_SENTENCES[1],
        "read_decision": "skip",
        "read_why": "Synthetic test input only; not a scientific reading recommendation.",
        "read_parts": [], "skip_parts": [], "estimated_read_minutes": 0,
        "original_value": "", "writing_friction": "low", "writing_reasons": [],
    }
    digest = quality_review._coerce_digest(json.dumps(raw))
    _review_identity.assert_review_source_unchanged(identity)
    entry = {"digest": digest.model_dump(mode="json"), "needs_pdf": False,
             "review_contract_version": _review_cache.REVIEW_CONTRACT_VERSION,
             "review_identity": identity}
    _review_cache._write_one(lab.stable, entry)
    current = _review_cache.get_current_review(lab.stable)
    assert current is not None and current["review_identity"] == identity
    assert current["digest"]["tldr"] == digest.tldr
    ready = review_eligibility.review_ready(original_row)
    assert ready
    assert _row(lab) == original_row, "Review input must not associate or label the row"
    receipt = {
        "origin": "parent_supplied_synthetic_not_model_generated", "item_key": lab.stable,
        "source_url": helpers.FEED_URL, "pdf_path": str(pdf), "page_count": 1,
        "source_sha256": identity["source_sha256"], "review_identity": identity,
        "generation_sha256_meaning": "cache compatibility fingerprint; not generation attestation",
        "declared_source_sentences": SOURCE_SENTENCES, "supplied_input": raw,
        "typed_input": entry["digest"], "current_contract_version": entry["review_contract_version"],
        "getter_observation": {"read_decision": current["digest"]["read_decision"],
                               "reading_policy_flags": current.get("reading_policy_flags", [])},
        "review_ready_observation": ready, "verifier": "unavailable_not_run",
        "semantic_acceptance": False, "human_acceptance": False,
        "quality_fields": "schema defaults only; not measurements or expert ratings",
    }
    helpers.write_json(lab.artifact / "explicit-add-fixture-origin.json", receipt)
    record_property("explicit_add_fixture_origin", json.dumps(receipt, sort_keys=True))


@pytest.fixture
def reviewed_question_browser(question_browser, request, record_property):
    lab = question_browser
    _source_receipt(lab, request, record_property)
    _supplied_review_input(lab, record_property)
    return lab


def _native_rows(lab):
    native_path = lab.settings.zotero_data_dir / "zotero.sqlite"
    assert native_path.resolve().is_relative_to(lab.artifact.resolve())
    return helpers.db_rows(native_path, "SELECT i.key,n.itemID,n.parentItemID,n.note,n.title "
                           "FROM itemNotes n JOIN items i ON i.itemID=n.itemID ORDER BY n.itemID")


def _snapshot(lab, key):
    with closing(sqlite3.connect(lab.database)) as conn:
        return review_note_projection.snapshot(conn, key)


def _projection(lab, key, original, unrelated, revision):
    desired = _snapshot(lab, key)
    assert desired["target_key"] == key and desired["pending"] is False
    assert desired["revision"] == revision > 0
    assert desired["body"] == MANUAL + "\n\nSaved questions:\n1. " + original["question"]
    notes = _native_rows(lab)
    owned = [row for row in notes if native_notes._owns_note(row["note"], pending.USER_NOTE_MARKER)]
    assert len(owned) == 1
    assert owned[0]["note"] == native_notes._native_note_html(pending.build_user_note_html(desired["body"]))
    native_path = lab.settings.zotero_data_dir / "zotero.sqlite"
    target = helpers.db_rows(native_path, "SELECT i.itemID,i.key,i.libraryID FROM items i JOIN libraries l "
                             "ON l.libraryID=i.libraryID WHERE i.key=? AND l.type='user'", (key,))
    assert len(target) == 1 and owned[0]["parentItemID"] == target[0]["itemID"]
    papers = helpers.db_rows(native_path, "SELECT i.key FROM items i JOIN libraries l ON l.libraryID=i.libraryID "
                            "JOIN itemTypes t ON t.itemTypeID=i.itemTypeID WHERE l.type='user' "
                            "AND t.typeName NOT IN ('note','attachment','annotation') ORDER BY i.key")
    assert {paper["key"] for paper in papers} == {helpers.LIBRARY_KEY, key} and len(papers) == 2
    assert [row for row in notes if row["key"] == "MANUAL01"] == unrelated
    assert browser._questions(lab) == [original]
    assert helpers.db_rows(lab.database, "SELECT note FROM review_notes WHERE item_key=?", (lab.stable,)) == [{"note": MANUAL}]
    assert helpers.db_rows(lab.database, "SELECT COUNT(*) AS n FROM sync_changes WHERE field='review_question'") == [{"n": 1}]
    assert list(lab.settings.zotero_data_dir.glob("zotero.sqlite.backup_*"))
    return desired, owned[0], target[0], papers


def _today_question(lab, page):
    from patchright.sync_api import expect

    page.goto(lab.server.origin + "/today")
    expect(page.get_by_text("Synthetic Today paper", exact=True)).to_be_visible()
    page.get_by_role("link", name="Open full review ↗", exact=True).click()
    expect(browser._notes_editor(page)).to_be_visible()
    browser._notes_editor(page).fill(MANUAL)
    with page.expect_response(lambda res: res.url.endswith("/api/golden/review-note")) as saved:
        page.get_by_role("button", name="Save", exact=True).click()
    assert saved.value.status == 200 and saved.value.json()["saved"] is True
    browser._notes_editor(page).fill(DIRTY)
    with page.expect_response(lambda res: res.url.endswith("/api/library/ask")) as answered:
        browser._submit(page)
    result = answered.value.json()
    assert answered.value.status == 200 and result["model"] == "deterministic-metadata"
    assert result["answer"] == "1 pages" and result["question_saved"] is True
    assert len(browser._questions(lab)) == 1
    original = browser._questions(lab)[0]
    assert original == result["question_submission"]
    assert str(UUID(original["submission_id"])) == original["submission_id"]
    assert original["item_key"] == lab.stable and original["origin"] == "typed"
    assert result["question_mirror"]["note_pending"] is True
    expect(browser._notes_editor(page)).to_have_value(DIRTY)
    expect(browser._saved_questions(page).get_by_text(original["question"], exact=True)).to_be_visible()
    return original


def _explicit_ui_add(lab, page, row):
    from patchright.sync_api import expect

    page.goto(lab.server.origin + "/today")
    page.get_by_role("checkbox", name="Select all", exact=True).check()
    add = page.get_by_role("button", name="Add 1 to library", exact=True)
    expect(add).to_be_enabled()
    with page.expect_response(lambda res: res.url.endswith(ADD_PATH)) as added:
        add.click()
    assert added.value.status == 200
    requests = [receipt for receipt in lab.server.receipts if receipt["method"] == "POST" and receipt["path"] == ADD_PATH]
    assert len(requests) == 1 and requests[0]["body"] == {"item_ids": [row["id"]], "target_collection_key": None}
    committed = _row(lab, row["id"])
    key = committed["materialized_zotero_key"]
    assert key and key not in {lab.stable, helpers.LIBRARY_KEY}
    assert committed["planned_zotero_key"] == key
    assert committed["stable_feed_key"] == row["stable_feed_key"] == lab.stable
    assert committed["decision"] == "selected" and committed["zotero_sync_status"] == "synced"
    return committed, added.value.json()


def _success(body, counter, expected):
    assert body[counter] == expected
    assert body["pending_sync"] == body["failed_count"] == 0
    assert body["failed"] == [] and body["zotero_sync_error"] is None


def _add_mirror(body, row, revision, written):
    assert len(body["note_mirrors"]) == 1
    mirror = body["note_mirrors"][0]
    assert mirror["id"] == row["id"]
    assert type(mirror["note_written"]) is bool and mirror["note_written"] is written
    assert mirror["note_error"] is None and mirror["note_pending"] is False
    assert type(mirror["projection_revision"]) is int
    assert mirror["projection_revision"] == revision > 0
    assert mirror["target_key"] == row["materialized_zotero_key"]


def test_today_explicit_add_preserves_question_uuid_manual_body_and_native_note(reviewed_question_browser):
    from patchright.sync_api import expect

    lab = reviewed_question_browser
    row = _row(lab)
    unrelated = [native for native in _native_rows(lab) if native["key"] == "MANUAL01"]
    assert len(unrelated) == 1
    page = lab.open_browser()
    original = _today_question(lab, page)
    asks = browser._post_count(lab, "/api/library/ask")
    assert asks == 1
    assert _row(lab) == row
    before = _snapshot(lab, lab.stable)
    assert before["target_key"] is None and before["pending"] is True and before["revision"] > 0
    revision = before["revision"]
    assert not [native for native in _native_rows(lab) if native_notes._owns_note(native["note"], pending.USER_NOTE_MARKER)]
    lab.checkpoint("explicit-add-before", page)

    add_page = lab.context.new_page()
    add_page.on("pageerror", lambda error: lab.page_errors.append(str(error)))
    committed, added = _explicit_ui_add(lab, add_page, row)
    _success(added, "added", 1)
    _add_mirror(added, committed, revision, True)
    assert browser._post_count(lab, "/api/library/ask") == asks
    key = committed["materialized_zotero_key"]
    baseline = _projection(lab, key, original, unrelated, revision)
    union = browser._http(add_page, "/api/library/questions?item_key=" + quote(key))
    assert union["status"] == 200 and union["body"]["questions"] == [original]
    expect(browser._notes_editor(page)).to_have_value(DIRTY)
    lab.checkpoint("explicit-add-materialized", add_page)

    repeated = browser._http(add_page, ADD_PATH, "POST", {"item_ids": [row["id"]], "target_collection_key": None})
    assert repeated["status"] == 200
    _success(repeated["body"], "added", 0)
    _add_mirror(repeated["body"], committed, revision, False)
    assert _row(lab, row["id"]) == committed
    assert _projection(lab, key, original, unrelated, revision) == baseline
    assert browser._post_count(lab, "/api/library/ask") == asks
    recovered = browser._http(add_page, "/api/feeds/review/apply-all", "POST", {})
    assert recovered["status"] == 200
    _success(recovered["body"], "applied", 0)
    assert _row(lab, row["id"]) == committed
    assert _projection(lab, key, original, unrelated, revision) == baseline
    assert browser._post_count(lab, "/api/library/ask") == asks

    add_page.goto(lab.server.origin + "/library")
    add_page.get_by_role("button").filter(has_text="Synthetic Today paper").first.click()
    expect(browser._notes_editor(add_page)).to_have_value(MANUAL)
    expect(browser._saved_questions(add_page).get_by_text(original["question"], exact=True)).to_be_visible()
    expect(browser._notes_editor(page)).to_have_value(DIRTY)
    lab.checkpoint("explicit-add-library", add_page)
    lab.checkpoint("explicit-add-dirty-preserved", page)
    assert browser._post_count(lab, "/api/library/ask") == asks

    lab.close_browser()
    lab.server.stop()
    lab.server.start()
    page = lab.open_browser()
    page.goto(lab.server.origin + "/paper/" + key)
    expect(browser._notes_editor(page)).to_have_value(MANUAL)
    expect(browser._saved_questions(page).get_by_text(original["question"], exact=True)).to_be_visible()
    union = browser._http(page, "/api/library/questions?item_key=" + quote(key))
    assert union["status"] == 200 and union["body"]["questions"] == [original]
    assert _row(lab, row["id"]) == committed
    assert _projection(lab, key, original, unrelated, revision) == baseline
    assert browser._post_count(lab, "/api/library/ask") == asks
    lab.checkpoint("explicit-add-restarted", page)
    helpers.write_json(lab.artifact / "explicit-add-observations.json", {
        "original_submission": original, "materialized_row": committed,
        "original_projection_revision": revision, "ui_add": added,
        "repeat_api": repeated, "recovery_api": recovered, "same_native_note": baseline[1],
        "same_native_parent": baseline[2], "qa_requests_after_explicit_ask": asks,
        "qa_requests_after_restart": browser._post_count(lab, "/api/library/ask"),
        "boundary": "real UI first Add; API repeat/empty recovery sweep; owned synthetic Zotero only",
    })
    print("QUESTION_BROWSER_ARTIFACT", lab.artifact)


def test_changed_source_after_extraction_rejects_before_cache_publication(
    question_browser, monkeypatch, request, record_property,
):
    import fitz

    lab = question_browser
    _source_receipt(lab, request, record_property)
    assert offline_requested()
    pdf = pdf_fetch.fetch_pdf(helpers.FEED_URL, cache_dir=lab.settings.pdf_cache_dir)
    assert pdf is not None
    pdf = pdf.resolve()
    assert pdf.is_relative_to(lab.settings.pdf_cache_dir.resolve())
    assert pdf.is_relative_to(lab.artifact.resolve())
    assert _review_cache.export_review_audit(lab.stable) is None
    real_coerce, real_write = quality_review._coerce_digest, _review_cache._write_one
    observed = {"origin": "controlled_owned_source_replacement_not_product_failure",
                "pdf_path": str(pdf), "coerce_calls": 0, "cache_writes": []}

    def replace_after_real_validation(raw):
        typed = real_coerce(raw)
        observed["coerce_calls"] += 1
        with fitz.open() as replacement:
            replacement.new_page().insert_text((50, 60), "Source-change regression B: replacement only.")
            pdf.write_bytes(replacement.tobytes())
        return typed

    def record_real_publication(key, entry):
        observed["cache_writes"].append({"key": key, "entry": entry})
        return real_write(key, entry)

    monkeypatch.setattr(quality_review, "_coerce_digest", replace_after_real_validation)
    monkeypatch.setattr(_review_cache, "_write_one", record_real_publication)
    try:
        with pytest.raises(ValueError) as rejected:
            _supplied_review_input(lab, record_property)
        observed.update(exception_type=type(rejected.value).__name__, exception_message=str(rejected.value))
        assert type(rejected.value) is ValueError
        assert str(rejected.value) == "PDF source changed during deep review"
        assert observed["coerce_calls"] == 1 and observed["cache_writes"] == []
    finally:
        observed["cache_after"] = _review_cache.export_review_audit(lab.stable)
        helpers.write_json(lab.artifact / "source-change-regression.json", observed)
        record_property("source_change_observation", json.dumps(observed, sort_keys=True))
        print("SOURCE_CHANGE_REGRESSION_ARTIFACT", lab.artifact)
    assert observed["cache_after"] is None
