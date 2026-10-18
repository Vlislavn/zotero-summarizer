"""Opt-in built Today/API/SQLite journey; synthetic evidence, not usefulness."""
import json
import os
import sqlite3

import pytest

from tests.test_application_browser_live import application_browser  # noqa: F401
from tests.test_review_reading_browser import CONTRAST, _keyboard_disclosure, _no_overflow
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.library import _review_cache, _review_identity
from zotero_summarizer.storage import feeds
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_APPLICATION_BROWSER_SMOKE") != "1", reason="opt-in real built Chromium",
)

EXPECTED = {
    "current-A": "Evidence: grade A (AI review)",
    "current-D": "Evidence: grade D (AI review)",
    "legacy": "Evidence: legacy grade A · current review unconfirmed",
    "unknown": "Review evidence: unknown",
    "stale": "Review evidence: unknown",
    "failed": "Review evidence: unknown",
    "needs-pdf": "Review: full text unavailable",
    "not-ready": "Review: not ready",
    "unscored": "Review evidence: unknown",
    "blinded": "Review evidence: unavailable (blinded selection)",
}


def _seed(settings, variant):
    config = get_context().state.app_state.config
    config.quality_review.rank_interleave = variant == "blinded"
    config.quality_review.rank_quality_first = False
    key = stable_feed_key_from_parts(guid="synthetic-selection")
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(
            conn, run_id="synthetic-selection", decision=feeds.DECISION_AWAITING_REVIEW,
            feed_item={"feed_library_id": 1, "item_id": 1, "guid": "synthetic-selection",
                       "title": "Synthetic Today paper"}, composite_score=3.0,
        )
        conn.execute("UPDATE processed_feed_items SET composite_score=?, surprise_score=?, "
                     "quality_review_json=? WHERE stable_feed_key=?",
                     (None if variant == "unscored" else 3.0, 5.0,
                      json.dumps({"grade": "A"}) if variant == "legacy" else None, key))
        conn.commit()
    if variant in {"current-A", "current-D", "needs-pdf", "not-ready", "stale", "failed"}:
        identity = _review_identity.build_review_identity(
            config=config, pdf_path="", source_kind="override", focus_prompt="",
        )
        entry = {"review_contract_version": _review_cache.REVIEW_CONTRACT_VERSION,
                 "review_identity": identity, "quality": {"grade": variant[-1] if variant.startswith("current") else "B"},
                 "digest": {"tldr": "Synthetic supplied storage evidence.", "read_decision": "skim"}}
        if variant == "needs-pdf":
            entry.update(needs_pdf=True, digest=None)
        elif variant == "not-ready":
            entry["digest"] = {}
        elif variant == "stale":
            identity["generation_sha256"] = "synthetic-obsolete-generation"
        elif variant == "failed":
            entry.update(error="synthetic attempt failure", digest=None)
        _review_cache._write_one(key, entry)
        assert _review_cache.review_is_current(entry, key) == (variant not in {"stale", "failed"})
    return key


def _rows(settings):
    with sqlite3.connect(settings.triage_db_path) as conn:
        return conn.execute("SELECT id,decision,composite_score,reading_priority "
                            "FROM processed_feed_items ORDER BY id").fetchall()


@pytest.mark.parametrize("variant", list(EXPECTED))
@pytest.mark.parametrize("width", [390, 1440])
def test_real_today_selection_evidence(application_browser, variant, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    key = _seed(settings, variant)
    before = _rows(settings)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto("http://127.0.0.1/today")
    article = page.get_by_role("article")
    expect(article).to_have_count(1)
    expect(article.get_by_text("Synthetic Today paper", exact=True)).to_be_visible()
    expect(article.get_by_text(EXPECTED[variant], exact=True)).to_be_visible()
    relevance = "Relevance: unscored" if variant == "unscored" else "Relevance: moderate"
    expect(article.get_by_text(relevance, exact=True)).to_be_visible()
    _keyboard_disclosure(page, "Why selected")
    response = client.get("/api/daily?K=5")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["papers"]) == 1 and payload["papers"][0]["stable_feed_key"] == key
    if variant == "blinded":
        assert payload["selection_policy"] == "interleave"
        assert payload["papers"][0]["selection_evidence"] == {"mode": "interleave", "basis": "unavailable_blinded"}
        assert article.get_by_text("Cohort key:", exact=True).count() == 0
    else:
        assert payload["selection_policy"] == "control"
        assert payload["papers"][0]["selection_evidence"]["stored"]["composite_recorded"] == (variant != "unscored")
        expect(article.get_by_text("Cohort key:", exact=True)).to_be_visible()
    after = _rows(settings)
    assert before == after
    contrast = page.evaluate(CONTRAST.replace(".paper-reading-page main", "article"))
    relevant = [row for row in contrast if row["text"] in {EXPECTED[variant], relevance, "Why selected"}
                or row["text"].startswith(("Active policy:", "Displayed composite:", "Stored priority (separate):",
                                          "Cohort ranking:", "Cohort key:", "Picker:", "Picker key:", "Quality adjustment:"))]
    assert len(relevant) >= 3
    overflow = _no_overflow(page)
    artifact = settings.data_dir / "today-selection-browser"
    artifact.mkdir(parents=True, exist_ok=True)
    stem = artifact / f"selection-{variant}-{width}"
    stem.with_suffix(".json").write_text(json.dumps({"variant": variant, "width": width,
        "scope": "real API/SQLite, synthetic cache; no ranking-usefulness claim", "payload": payload,
        "contrast": relevant, "overflow": overflow, "before": before, "after": after}, indent=2))
    page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
    article.get_by_role("link", name="Open full review ↗", exact=True).click()
    expect(page.get_by_role("heading", name="Synthetic Today paper", exact=True)).to_be_visible()
    assert not [row for row in seen if row[0] == "POST"
                and row[1] in {"/api/daily/add-to-library", "/api/daily/trash", "/api/library/ask"}]
    assert overflow["scroll"] <= overflow["client"], overflow
    assert not [row for row in relevant if row["ratio"] < 4.5], relevant
