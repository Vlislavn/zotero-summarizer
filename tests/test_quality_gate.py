"""The auto QUALITY GATE — quality as a conjunctive hard filter (precision mode).

The user's philosophy: ``quality ∧ match``, quality is a HARD filter. A bad paper
on-topic must NOT surface. This tests the gate's two layers + the safety properties:
L1 (LLM score floor) / L2 (grade D | band flag) hide; None signals + red_flags-alone
do NOT hide; a human verdict is never clobbered; a manual relabel restores.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.domain import VERDICT_SOURCE_AUTO_QUALITY, VERDICT_SOURCE_USER
from zotero_summarizer.services.library.quality_gate import (
    apply_auto_quality_gate,
    should_auto_hide,
)
from zotero_summarizer.storage.repositories import (
    get_label_verdict,
    init_db,
    insert_or_update_label_verdict,
    list_all_label_verdicts,
    with_db_path,
)


def _gate_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "triage_history.db"
    with with_db_path(db_path):
        init_db()
    return db_path


def _shap(llm_score: int | None) -> str:
    summary = {} if llm_score is None else {"relevance_score": llm_score}
    return json.dumps({"shap": [], "aux_context": {}, "summary": summary})


_SEED_SEQ = 0


def _seed_row(db_path: Path, *, item_key: str, priority: str, llm_score: int | None) -> None:
    """Insert a shown processed_feed_items row with a controlled LLM score."""
    global _SEED_SEQ
    _SEED_SEQ += 1
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO processed_feed_items
               (feed_library_id, feed_item_id, guid, title, decision, run_id,
                reading_priority, materialized_zotero_key, shap_contribs_json)
               VALUES (1, ?, ?, ?, 'triaged_pending', 't', ?, ?, ?)""",
            (_SEED_SEQ, item_key, item_key, priority, item_key, _shap(llm_score)),
        )
        conn.commit()
    finally:
        conn.close()


def _reviews(quality_for: dict[str, dict]) -> dict:
    return {k: {"quality": v} for k, v in quality_for.items()}


@pytest.mark.parametrize("caller", ["deep_review", "fleet", "weekly"])
def test_review_callers_preserve_labels_until_explicit_triage_gate(monkeypatch, caller):
    from types import SimpleNamespace

    from test_deep_review import _StubExtractor, _StubReader, _detail, _wire
    from zotero_summarizer.services._common import settings
    from zotero_summarizer.services.library import app_library_reader, deep_review
    from zotero_summarizer.services.library.review_fleet import fleet, verdict_store
    from zotero_summarizer.services.research_feed import runner
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services.triage.feeds import _tick

    db = _gate_db(settings().data_dir)
    assert db == settings().triage_db_path
    _seed_row(db, item_key="REVIEWED", priority="could_read", llm_score=5)
    _seed_row(db, item_key="UNRELATED", priority="could_read", llm_score=1)
    insert_or_update_label_verdict(
        db, item_key="HUMAN", original_derived_priority="could_read",
        user_priority="should_read", comment="keep", source=VERDICT_SOURCE_USER,
    )
    before = list_all_label_verdicts(db)
    for name, value in (("ZS_AUTO_QUALITY_GATE", "1"), ("ZS_AUTO_QUALITY_LLM_FLOOR", "2"),
                        ("ZS_AUTO_QUALITY_HIDE_GRADES", "D"), ("ZS_AUTO_QUALITY_HIDE_BANDS", "flag")):
        monkeypatch.setenv(name, value)
    reader = _StubReader({"REVIEWED": _detail()})
    _wire(monkeypatch, _default_goals_config(), reader=reader, extractor=_StubExtractor())
    monkeypatch.setattr(app_library_reader, "AppLibraryReader", lambda db: reader)
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers",
                        lambda ctx: ({"grade": "D"}, [], None, None, None))
    monkeypatch.setattr(deep_review, "_JOBS", {})
    monkeypatch.setattr(deep_review, "_ensure_pool", lambda provider: SimpleNamespace(
        submit=lambda fn, *args: fn(*args),
    ))
    monkeypatch.setattr(fleet, "_LATCH", fleet._flight.FlightLatch())
    monkeypatch.setattr(fleet, "_STATE", dict(fleet._STATE))
    monkeypatch.setattr(fleet._flight, "run_in_background", lambda fn: fn())

    assert deep_review.get_cached_review("REVIEWED") is None
    if caller == "fleet":
        result = fleet.start(item_keys=["REVIEWED"])
        assert result["status"] == "ready" and result["proposed"] == 1
        assert set(verdict_store.read_all()) == {"REVIEWED"}
    elif caller == "weekly":
        runner._ensure_reviews(settings(), [SimpleNamespace(source_id="REVIEWED")], 30)
    else:
        deep_review.start(item_keys=["REVIEWED"])

    assert deep_review.status("REVIEWED")["status"] == "ready"
    assert deep_review.get_cached_review("REVIEWED")["quality"]["grade"] == "D"
    assert list_all_label_verdicts(db) == before
    _tick._maybe_auto_quality_gate("test-dry-run", dry_run=True)
    assert list_all_label_verdicts(db) == before
    monkeypatch.setenv("ZS_AUTO_QUALITY_GATE", "0")
    _tick._maybe_auto_quality_gate("test-disabled", dry_run=False)
    assert list_all_label_verdicts(db) == before
    monkeypatch.setenv("ZS_AUTO_QUALITY_GATE", "1")
    _tick._maybe_auto_quality_gate("test-explicit-triage", dry_run=False)
    assert get_label_verdict(db, "REVIEWED")["source"] == VERDICT_SOURCE_AUTO_QUALITY
    assert get_label_verdict(db, "UNRELATED")["source"] == VERDICT_SOURCE_AUTO_QUALITY
    assert get_label_verdict(db, "HUMAN")["user_priority"] == "should_read"


# --- pure layer logic ------------------------------------------------------


def test_l1_llm_score_floor_hides_low_keeps_rest():
    assert should_auto_hide(relevance_score=2, quality=None) == (True, "L1 llm_score 2<=2")
    assert should_auto_hide(relevance_score=1, quality=None)[0] is True
    assert should_auto_hide(relevance_score=3, quality=None) == (False, "")
    assert should_auto_hide(relevance_score=5, quality=None) == (False, "")


def test_l2_grade_and_band_hide_even_with_high_llm_score():
    # full-text bad quality outranks the abstract-based LLM score
    assert should_auto_hide(relevance_score=5, quality={"grade": "D"})[0] is True
    assert should_auto_hide(relevance_score=5, quality={"quality_band": "flag"})[0] is True


def test_l2_keeps_good_and_borderline_quality():
    assert should_auto_hide(relevance_score=4, quality={"grade": "A"}) == (False, "")
    assert should_auto_hide(relevance_score=4, quality={"grade": "B"}) == (False, "")
    # C / uncertain / neutral are NOT auto-hidden (left for the human)
    assert should_auto_hide(relevance_score=4, quality={"grade": "C"}) == (False, "")
    assert should_auto_hide(relevance_score=4, quality={"quality_band": "uncertain"}) == (False, "")
    assert should_auto_hide(relevance_score=4, quality={"quality_band": "neutral"}) == (False, "")


def test_red_flags_alone_do_not_hide():
    # red_flags / overstatements only cut the fleet confidence + render a ⚠️ chip;
    # hiding on them alone is too aggressive — they need a D grade or flag band.
    q = {"grade": "C", "quality_band": "neutral", "red_flags": ["x"], "overstatements": ["y"]}
    assert should_auto_hide(relevance_score=4, quality=q) == (False, "")


def test_none_signals_never_hide():
    # absence of evidence is never evidence to hide (the documented contract)
    assert should_auto_hide(relevance_score=None, quality=None) == (False, "")
    assert should_auto_hide(relevance_score=None, quality={"grade": ""}) == (False, "")


# --- the applied gate (DB) -------------------------------------------------


def test_gate_hides_bad_quality_and_writes_auto_source(tmp_path):
    db = _gate_db(tmp_path)
    _seed_row(db, item_key="GOOD_A", priority="could_read", llm_score=4)
    _seed_row(db, item_key="BAD_D", priority="could_read", llm_score=5)
    _seed_row(db, item_key="BAD_FLAG", priority="should_read", llm_score=5)
    _seed_row(db, item_key="LOW_LLM", priority="could_read", llm_score=2)
    reviews = _reviews({"BAD_D": {"grade": "D"}, "BAD_FLAG": {"quality_band": "flag"}})

    hidden = apply_auto_quality_gate(db, reviews)

    assert hidden == 3
    assert get_label_verdict(db, "GOOD_A") is None          # kept
    assert get_label_verdict(db, "BAD_D")["user_priority"] == "dont_read"
    assert get_label_verdict(db, "BAD_D")["source"] == VERDICT_SOURCE_AUTO_QUALITY
    assert get_label_verdict(db, "BAD_FLAG")["user_priority"] == "dont_read"
    assert get_label_verdict(db, "LOW_LLM")["user_priority"] == "dont_read"


def test_gate_never_clobbers_a_user_verdict(tmp_path):
    db = _gate_db(tmp_path)
    # a human already decided could_read on a D-grade paper — the gate must NOT hide it
    _seed_row(db, item_key="USER_KEPT", priority="could_read", llm_score=5)
    insert_or_update_label_verdict(
        db, item_key="USER_KEPT", original_derived_priority="could_read",
        user_priority="could_read", comment="human keep", source=VERDICT_SOURCE_USER,
    )
    reviews = _reviews({"USER_KEPT": {"grade": "D"}})

    hidden = apply_auto_quality_gate(db, reviews)

    assert hidden == 0
    # the human verdict is intact
    v = get_label_verdict(db, "USER_KEPT")
    assert v["user_priority"] == "could_read"
    assert v["source"] == VERDICT_SOURCE_USER


def test_manual_relabel_restores_an_auto_hidden_row(tmp_path):
    db = _gate_db(tmp_path)
    _seed_row(db, item_key="AUTO_D", priority="could_read", llm_score=5)
    reviews = _reviews({"AUTO_D": {"grade": "D"}})

    apply_auto_quality_gate(db, reviews)
    assert get_label_verdict(db, "AUTO_D")["source"] == VERDICT_SOURCE_AUTO_QUALITY

    # the user overrides: a manual could_read flips the row back to user source
    insert_or_update_label_verdict(
        db, item_key="AUTO_D", original_derived_priority="could_read",
        user_priority="could_read", comment="user restore", source=VERDICT_SOURCE_USER,
    )
    v = get_label_verdict(db, "AUTO_D")
    assert v["user_priority"] == "could_read"
    assert v["source"] == VERDICT_SOURCE_USER  # the UPSERT overwrote the auto source


def test_gate_rejects_unparseable_payload(tmp_path):
    db = _gate_db(tmp_path)
    # one corrupt row (malformed JSON) + one clean hideable row
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO processed_feed_items
           (feed_library_id, feed_item_id, guid, title, decision, run_id,
            reading_priority, materialized_zotero_key, shap_contribs_json)
           VALUES (1, 0, 'CORRUPT', 'CORRUPT', 'triaged_pending', 't',
                   'could_read', 'CORRUPT', 'not-json{')""")
    conn.commit()
    conn.close()
    _seed_row(db, item_key="CLEAN_D", priority="could_read", llm_score=5)
    reviews = _reviews({"CLEAN_D": {"grade": "D"}})

    with pytest.raises(json.JSONDecodeError):
        apply_auto_quality_gate(db, reviews)
    assert get_label_verdict(db, "CORRUPT") is None


def test_gate_runner_propagates_cache_read_failure(monkeypatch):
    from zotero_summarizer.services.library import deep_review, quality_gate

    def unreadable():
        raise OSError("review cache unavailable")

    monkeypatch.setenv("ZS_AUTO_QUALITY_GATE", "1")
    monkeypatch.setattr(deep_review, "_read_all", unreadable)
    with pytest.raises(OSError, match="review cache unavailable"):
        quality_gate.fire_full()


def test_disabled_config_hides_nothing(tmp_path):
    db = _gate_db(tmp_path)
    _seed_row(db, item_key="BAD_D", priority="could_read", llm_score=2)
    reviews = _reviews({"BAD_D": {"grade": "D"}})

    # empty hide-sets + a floor nothing can hit → no hides (the "gate OFF" contract)
    hidden = apply_auto_quality_gate(
        db, reviews, llm_floor=0, hide_grades=(), hide_bands=(),
    )
    assert hidden == 0
    assert list_all_label_verdicts(db) == []


# --- the list_verdicts source filter (route layer) ------------------------
# The auto-gate's hides carry source=auto_quality; list_verdicts?source=... lets
# the UI surface + restore them without paging every dont_read. This proves the
# post-filter on v.get("source") against real mixed-provenance rows.


def test_list_verdicts_source_filter_isolates_auto_quality_hides(tmp_path, monkeypatch):
    import asyncio

    from zotero_summarizer.api.routes import golden as golden_routes

    db = _gate_db(tmp_path)
    # one auto-quality hide, one user reject, one machine_add — all dont_read
    for key, src in (
        ("AUTO_HIDE", VERDICT_SOURCE_AUTO_QUALITY),
        ("USER_REJECT", VERDICT_SOURCE_USER),
        ("MACHINE_ADD", "machine_add"),
    ):
        insert_or_update_label_verdict(
            db, item_key=key, original_derived_priority="could_read",
            user_priority="dont_read", comment="", source=src,
        )

    monkeypatch.setattr(golden_routes, "_db_path", lambda: db)

    auto_only = asyncio.run(golden_routes.list_verdicts(source=VERDICT_SOURCE_AUTO_QUALITY))
    assert [v["item_key"] for v in auto_only["verdicts"]] == ["AUTO_HIDE"]
    assert auto_only["total"] == 1

    user_only = asyncio.run(golden_routes.list_verdicts(source=VERDICT_SOURCE_USER))
    assert [v["item_key"] for v in user_only["verdicts"]] == ["USER_REJECT"]

    # no filter → all three, most-recent-first
    all_v = asyncio.run(golden_routes.list_verdicts())
    assert all_v["total"] == 3

    # a source with no matches returns empty, not an error
    none_v = asyncio.run(golden_routes.list_verdicts(source="nonexistent"))
    assert none_v["verdicts"] == [] and none_v["total"] == 0
