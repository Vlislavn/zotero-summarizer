"""Scientific grade authority through real review/projection/note boundaries.

Only external work and native delivery are substituted. No live scientific or
native-editor acceptance is implied by these synthetic rendering regressions.
"""
from copy import deepcopy
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from zotero_summarizer.models import GoalSummary, PaperDigest, QualityEval
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.library.review_fleet import propose

SOURCE = {
    "tldr": "A reusable concept.", "read_decision": "read", "read_why": "Inspect the decisive comparison.",
    "read_parts": ["§2, Fig. 1"], "skip_parts": ["Appendix A"], "estimated_read_minutes": 8,
    "original_value": "A decision-changing comparison.", "basis": "full_text", "grade": "A",
    "novelty": 5, "significance": 4, "writing_friction": "low", "writing_reasons": [],
}
HIT = {"goal": "Evaluation", "retrieval_state": "hit", "relevant": True, "abstained": False}


@pytest.mark.parametrize("assessment, expected_grade, action", [
    ("flag", "D", "skim"), ("non_paper", "", "read"),
    ("not_assessed", "", "skim"), ("exception", "", "skim"),
])
def test_evaluator_to_worker_cache_proposal_and_saved_note_share_grade(
    tmp_path, monkeypatch, assessment, expected_grade, action,
):
    from zotero_summarizer.services.library import (
        _code_link, _paper_goal_summaries, _paper_read_html,
        _paper_read_pdf, _review_identity, _review_section_overlay, deep_review, paper_type, quality_eval,
    )
    from zotero_summarizer.services.library.review_fleet import fleet, verdict_store
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services.zotero import zotero
    from zotero_summarizer.services.zotero._notes import build_digest_note_html

    source = PaperDigest(**SOURCE)
    before = source.model_dump()
    config = _default_goals_config()
    config.research_goals = ["Evaluation"]
    note_calls, quality_calls, proposals = [], [], []
    paper_kind = "non_paper" if assessment == "non_paper" else "position"

    def evaluate(**kwargs):
        quality_calls.append(kwargs)
        if assessment == "exception":
            raise RuntimeError("Synthetic independent evaluator failure")
        if assessment == "flag":
            return QualityEval(grade="D", quality_band="flag", paper_type=paper_kind)
        return QualityEval(grade="", basis=assessment, paper_type=paper_kind,
                           quality_band="" if assessment == "non_paper" else "uncertain")

    def capture_note(key, digest):
        note_calls.append((key, digest.model_dump(), build_digest_note_html(digest)))

    monkeypatch.setattr(deep_review._map_reduce, "digest_for_strategy", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(_paper_read_pdf, "extract_pdf_content", lambda *_args, **_kwargs: {"full_text": "A concept."})
    monkeypatch.setattr(paper_type, "detect_safe", lambda *_args: {"type": paper_kind, "uncertain": False})
    monkeypatch.setattr(quality_eval, "evaluate_quality", evaluate)
    monkeypatch.setattr(_paper_goal_summaries, "summarize_for_goals", lambda **_kwargs: [GoalSummary(**HIT)])
    monkeypatch.setattr(_code_link, "find_code_link", lambda *_args: {"found": False})
    monkeypatch.setattr(_review_section_overlay, "build_section_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_review_identity, "build_review_identity", lambda **_kwargs: {"generation_sha256": "fixture"})
    monkeypatch.setattr(zotero, "zotero_upsert_digest_note", capture_note)
    cache_path = tmp_path / "reviews.json"
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: cache_path)
    reader = SimpleNamespace(get_item_detail=lambda _key: {"title": "Study", "pdf_path": "/fixture.pdf"})

    entry = deep_review._review_one({"item_key": "PAPER001"}, reader=reader, config=config,
                                   extractor=SimpleNamespace(extract_text=lambda _path: "A concept."),
                                   llm=object(), quality_enabled=True, lean_tier=True)
    _review_cache._write_one("PAPER001", entry)
    saved_bytes = cache_path.read_bytes()
    projected = _review_cache.get_cached_review("PAPER001")
    monkeypatch.setattr(fleet, "_current_cache", lambda _key: projected)
    monkeypatch.setattr(verdict_store, "review_fingerprint", lambda _review: "fixture")
    monkeypatch.setattr(verdict_store, "upsert", lambda key, data: proposals.append((key, data)))
    assert fleet._propose_for_item("PAPER001", login={}) == "proposed"

    assert len(quality_calls) == 1 and len(note_calls) == 1
    assert projected["model_digest"] == before == source.model_dump()
    assert projected["digest"]["grade"] == entry["digest"]["grade"] == expected_grade
    assert projected["digest"]["read_decision"] == action
    assert proposals[0][0] == note_calls[0][0] == "PAPER001"
    assert proposals[0][1]["grade"] == expected_grade
    assert proposals[0][1]["proposed"] == ("should_read" if action == "read" else "could_read")
    assert proposals[0][1]["digest_read_decision"] == "read"
    assert note_calls[0][1]["grade"] == expected_grade
    assert note_calls[0][1]["read_decision"] == action
    note = ET.fromstring("<root>" + note_calls[0][2] + "</root>")
    assert note.find("h2").text.endswith(" · Quality " + (expected_grade or "—"))
    assert "quality " + (expected_grade or "—") in note.findall("p")[-1].find("em").text
    assert "Quality A" not in note_calls[0][2] and "quality A" not in note_calls[0][2]
    assert cache_path.read_bytes() == saved_bytes
    assert _review_cache._read_all()["PAPER001"] == entry
    assert propose.project_review(projected) == projected
    if assessment == "exception":
        assert entry["quality"] is not None
        assert entry["quality"]["basis"] == "not_assessed"
        assert entry["quality"]["quality_band"] == "uncertain"
        assert entry["quality"]["grade"] == "" and entry["quality"]["coverage_fraction"] is None
        assert entry["quality"]["red_flags"] == entry["quality"]["missing_critical"] == []
        assert "weak_evidence" not in projected["reading_policy_flags"]
        assert "quality_uncertain" in proposals[0][1]["flags"]
        assert proposals[0][1]["confidence"] < 0.6
        presentation = _paper_read_html._render_presentation({"title": "Study"}, projected["digest"],
                                                          projected["quality"], projected["goal_summaries"])
        assert "Evidence: UNCERTAIN" in presentation
        assert '<div class="v-word">SKIM · 8 MIN</div>' in presentation


@pytest.mark.parametrize("quality, expected", [
    (None, "A"), ({"quality_band": "neutral"}, "A"),
    ({"basis": "full_text", "grade": "D"}, "D"),
    ({"basis": "full_text", "grade": ""}, ""),
    ({"basis": "non_paper", "grade": "A"}, ""),
    ({"basis": "not_assessed", "grade": "A"}, ""),
])
def test_applied_grade_and_proposal_share_authority_without_mutating_model(quality, expected):
    original = deepcopy((SOURCE, quality))
    applied, raw, _ = propose.apply_reading_policy(SOURCE, quality, [HIT])
    proposal = propose.propose_verdict(SOURCE, quality, goal_summaries=[HIT])
    assert applied["grade"] == proposal.grade == expected
    assert raw == "read" and (SOURCE, quality) == original


def test_only_real_legacy_absence_can_fall_back_to_digest_self_grade(tmp_path, monkeypatch):
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "reviews.json")
    legacy = {"digest": SOURCE, "quality": None, "goal_summaries": [HIT]}
    retained = {**legacy, "model_digest": SOURCE}
    _review_cache._write_one("LEGACY01", legacy)
    _review_cache._write_one("RETAIN01", retained)
    before = _review_cache._read_all()

    old = _review_cache.get_cached_review("LEGACY01")
    new = _review_cache.get_cached_review("RETAIN01")

    assert old["digest"]["grade"] == "A" and old["quality"] is None
    assert new["digest"]["grade"] == "" and new["model_digest"]["grade"] == "A"
    assert new["quality"]["basis"] == "not_assessed" and new["quality"]["grade"] == ""
    assert propose.propose_verdict(new["model_digest"], new["quality"], goal_summaries=[HIT]).grade == ""
    assert _review_cache._read_all() == before
