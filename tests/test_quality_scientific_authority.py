"""Scientific grade authority through real review/projection/note boundaries.

External analysis is scripted; proposal approval and note delivery use owned
native SQLite. No live scientific or native-editor acceptance is implied.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from tests._reading_contract import Document
from tests.test_digest_verification import _Verifier
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
ORIGINAL_TEXT = 'A reusable concept.\n§2, Fig. 1: The decisive comparison.\nAppendix A.'


@pytest.mark.parametrize("assessment, expected_grade, action", [
    ("flag", "D", "skim"), ("non_paper", "", "read"),
    ("not_assessed", "", "skim"), ("exception", "", "skim"),
])
def test_evaluator_to_worker_cache_proposal_and_saved_note_share_grade(
    tmp_path, monkeypatch, assessment, expected_grade, action,
):
    from zotero_summarizer.services.library import (
        _code_link, _paper_goal_summaries, _paper_read_html,
        _paper_read_pdf, _review_section_overlay, deep_review, paper_type, quality_eval,
    )
    from zotero_summarizer.services.library.review_fleet import fleet, verdict_store
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    import asyncio
    from tests._generated_write_fixtures import install_generated_writer
    from tests._zotero_fixtures import add_library_item
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.models import PendingChangeApplyRequest
    from zotero_summarizer.services.library import _review_identity
    from zotero_summarizer.services.zotero import pending

    source = PaperDigest(**SOURCE)
    before = source.model_dump()

    class SourceClient(_Verifier):
        def __init__(self):
            super().__init__()
            self.generated = 0

        def pydantic_prompt(self, *, pydantic_model, **kwargs):
            if pydantic_model is PaperDigest:
                self.generated += 1
                return source.model_copy(deep=True)
            return super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)

    client = SourceClient()
    config = _default_goals_config()
    config.research_goals = ["Evaluation"]
    quality_calls, proposals = [], []
    paper_kind = "non_paper" if assessment == "non_paper" else "position"

    def evaluate(**kwargs):
        quality_calls.append(kwargs)
        if assessment == "exception":
            raise RuntimeError("Synthetic independent evaluator failure")
        if assessment == "flag":
            return QualityEval(grade="D", quality_band="flag", paper_type=paper_kind)
        return QualityEval(grade="", basis=assessment, paper_type=paper_kind,
                           quality_band="" if assessment == "non_paper" else "uncertain")

    monkeypatch.setattr(_paper_read_pdf, "extract_pdf_content", lambda *_args, **_kwargs: {"full_text": ORIGINAL_TEXT})
    monkeypatch.setattr(paper_type, "detect_safe", lambda *_args: {"type": paper_kind, "uncertain": False})
    monkeypatch.setattr(quality_eval, "evaluate_quality", evaluate)
    monkeypatch.setattr(_paper_goal_summaries, "summarize_for_goals", lambda **_kwargs: [GoalSummary(**HIT)])
    monkeypatch.setattr(_code_link, "find_code_link", lambda *_args: {"found": False})
    monkeypatch.setattr(_review_section_overlay, "build_section_overlay", lambda *_args, **_kwargs: None)
    db, writer = install_generated_writer(tmp_path, monkeypatch)
    add_library_item(db, item_key="PAPER001", title="Study")
    monkeypatch.setattr(writer, "apply_changes", Mock(wraps=writer.apply_changes))
    monkeypatch.setattr(_review_identity, "state", lambda: SimpleNamespace(app_state=SimpleNamespace(config=config)))
    monkeypatch.setattr(deep_review, "_try_rebuild_render", Mock())
    cache_path = tmp_path / "reviews.json"
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: cache_path)
    path = tmp_path / "fixture.pdf"
    path.write_bytes(b"Synthetic original bytes; extraction is controlled by the fixture")
    reader = SimpleNamespace(get_item_detail=lambda _key: {"title": "Study", "pdf_path": str(path)})

    deep_review._review_worker({"item_key": "PAPER001", "pdf_path": str(path)}, {
        "reader": reader, "config": config,
        "extractor": SimpleNamespace(extract_text=lambda _path: ORIGINAL_TEXT),
        "llm": client, "quality_enabled": True, "lean_tier": True,
    }, "")
    assert deep_review.status("PAPER001")["status"] == "ready"
    entry = _review_cache._read_all(readonly=True)["PAPER001"]
    writer.apply_changes.assert_not_called()
    assert entry["zotero_note_written"] is False and entry["zotero_note_error"] is None
    saved_bytes = cache_path.read_bytes()
    projected = _review_cache.get_cached_review("PAPER001")
    monkeypatch.setattr(fleet, "_current_cache", lambda _key: projected)
    monkeypatch.setattr(verdict_store, "review_fingerprint", lambda _review: "fixture")
    monkeypatch.setattr(verdict_store, "upsert", lambda key, data: proposals.append((key, data)))
    assert fleet._propose_for_item("PAPER001", login={}) == "proposed"

    assert len(quality_calls) == 1
    assert (client.generated, client.calls) == (1, 1)
    assert entry['verification_audit'] is not None
    assert _review_cache.get_current_review('PAPER001') == projected
    assert projected["model_digest"] == before == source.model_dump()
    assert projected["digest"]["grade"] == entry["digest"]["grade"] == expected_grade
    assert projected["digest"]["read_decision"] == action
    assert proposals[0][0] == "PAPER001"
    assert proposals[0][1]["grade"] == expected_grade
    assert proposals[0][1]["proposed"] == ("should_read" if action == "read" else "could_read")
    assert proposals[0][1]["digest_read_decision"] == "read"
    row, = asyncio.run(pending.list_pending_changes(item_key="PAPER001")).items
    approved = PendingChangeApplyRequest(change_ids=[row["id"]], previews={
        row["id"]: row["approval_preview"]["preview_sha256"]})
    assert asyncio.run(pending.apply_pending_changes(approved))["applied"] == 1
    writer.apply_changes.assert_called_once()
    notes = ZoteroReader(db.parent).get_item_notes("PAPER001")
    assert len(notes) == 1
    note_html = notes[0]["note"]
    fragment = row["approval_preview"]["payload"]["note_html"]
    assert json.loads(row["payload_json"]) == row["approval_preview"]["payload"]
    assert note_html == '<div class="zotero-note znv1">' + fragment + '</div>'
    note = ET.fromstring(note_html)
    assert note.find("h2").text.endswith(" · Quality " + (expected_grade or "—"))
    assert "quality " + (expected_grade or "—") in note.findall("p")[-1].find("em").text
    assert "Quality A" not in note_html and "quality A" not in note_html
    assert asyncio.run(pending.apply_pending_changes(approved))["applied"] == 0
    writer.apply_changes.assert_called_once()
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
        words = [node.text() for node in Document(presentation).root.nodes() if node.attrs.get("class") == "v-word"]
        assert words == ["SKIM · 8 MIN"]


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
