"""Observable projections and pure policy; scripted evidence is not human validation."""
from copy import deepcopy
from html import escape
from types import SimpleNamespace

import pytest

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import (
    _paper_read_brief as brief, _paper_read_html as html, _paper_type_checklists as pc,
    _review_cache, paper_type, quality_eval, quality_review,
)
from zotero_summarizer.services.library.review_fleet import propose

HIT = [{"goal": "Evaluation", "retrieval_state": "hit", "relevant": True, "abstained": False}]
DIGEST = {
    "tldr": "A reusable concept.", "read_decision": "read", "read_why": "Inspect the decisive comparison.",
    "read_parts": ["§2, Fig. 1"], "skip_parts": ["Appendix A"], "estimated_read_minutes": 8,
    "original_value": "A decision-changing comparison.", "basis": "full_text", "grade": "A",
    "novelty": 5, "significance": 4, "writing_friction": "low", "writing_reasons": [],
}
QUALITY = {"quality_band": "highlight", "basis": "full_text", "grade": "B", "paper_type": "position"}


@pytest.mark.parametrize("changes, quality, expected, flag", [
    ({}, QUALITY, "read", None),
    ({"writing_friction": "high", "writing_reasons": ["§2 uses the central state before defining it."]},
     QUALITY, "skim", "writing_friction"),
    ({}, {**QUALITY, "quality_band": "flag"}, "skim", "weak_evidence"),
    ({}, {**QUALITY, "overstatements": ["A proposal presented as validated."]}, "skim", "weak_evidence"),
    ({"novelty": 2, "significance": 2, "clarity": 5}, QUALITY, "skim", "idea_value_not_high"),
    ({"writing_friction": None}, QUALITY, "skim", "writing_not_assessed"),
    ({"basis": "abstract"}, QUALITY, "skim", "full_text_not_confirmed"),
    ({"original_value": ""}, QUALITY, "skim", "original_value_not_assessed"),
    ({}, {**QUALITY, "basis": "not_assessed"}, "skim", "strong_evidence_not_confirmed"),
    ({}, {**QUALITY, "quality_band": "uncertain"}, "skim", "strong_evidence_not_confirmed"),
])
def test_read_intersection_preserves_the_idea_and_independent_signals(changes, quality, expected, flag):
    source = {**DIGEST, **changes}
    before = deepcopy((source, quality, HIT))

    applied, raw, flags = propose.apply_reading_policy(source, quality, HIT)

    assert applied["read_decision"] == expected and raw == "read"
    assert applied["read_parts"] == source["read_parts"]
    assert applied["novelty"] == source["novelty"]
    assert (source, quality, HIT) == before
    assert (flag in flags) if flag else not flags
    if flag == "weak_evidence":
        assert "claims not established" in applied["read_why"]


def test_cap_without_legacy_targets_withholds_instead_of_inventing_a_skim():
    applied, raw, flags = propose.apply_reading_policy(
        {**DIGEST, "read_parts": [], "writing_friction": "high"}, QUALITY, HIT,
    )
    assert raw == "read" and applied["read_decision"] == ""
    assert "reading_targets_not_assessed" in flags
    assert applied["tldr"] == DIGEST["tldr"]


def test_existing_skim_is_qualified_when_evidence_is_weak_and_projection_is_idempotent():
    source = {**DIGEST, "read_decision": "skim"}
    quality = {**QUALITY, "quality_band": "flag"}
    applied, _, flags = propose.apply_reading_policy(source, quality, HIT)
    repeated, _, repeated_flags = propose.apply_reading_policy(applied, quality, HIT)
    assert applied == repeated and flags == repeated_flags == ["weak_evidence"]
    assert "claims not established" in applied["read_why"]
    assert source["read_why"] == DIGEST["read_why"]


@pytest.mark.parametrize("grade, basis, expected", [("D", "full_text", "D"), ("", "not_assessed", ""),
                                                   ("", "non_paper", "")])
def test_independent_quality_grade_wins_including_explicit_ungraded(grade, basis, expected):
    result = propose.propose_verdict(DIGEST, {**QUALITY, "grade": grade, "basis": basis}, goal_summaries=HIT)
    assert result.grade == expected
    if grade == "D":
        assert result.proposed == "should_read"


def test_new_schema_preserves_raw_high_friction_action_and_legacy_unknown():
    source = {**DIGEST, "writing_friction": "high", "writing_reasons": ["§2 omits a state transition."]}
    parsed = quality_review._coerce_digest(source)
    assert parsed.read_decision == "read"
    assert propose.apply_reading_policy(parsed.model_dump(), QUALITY, HIT)[0]["read_decision"] == "skim"
    assert PaperDigest.model_validate({"read_decision": "read"}).writing_friction is None
    with pytest.raises(ValueError, match="explicit writing assessment"):
        quality_review._coerce_digest({**DIGEST, "writing_friction": None})


def test_cache_reads_and_current_bulk_projection_preserve_raw_bytes_and_repeat_safely(tmp_path, monkeypatch):
    path = tmp_path / "deep_reviews.json"
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: path)
    monkeypatch.setattr(_review_cache, "review_is_current", lambda *_args: True)
    model = {**DIGEST, "writing_friction": "high", "writing_reasons": ["§2 has an undefined construct."]}
    entry = {"model_digest": model, "digest": model, "quality": QUALITY, "goal_summaries": HIT}
    _review_cache._write_one("PAPER001", entry)
    before = path.read_bytes()

    projected = _review_cache.get_current_review("PAPER001")
    bulk = _review_cache.current_reviews()["PAPER001"]

    assert projected == bulk == _review_cache.get_cached_review("PAPER001")
    assert projected["digest"]["read_decision"] == "skim"
    assert projected["model_digest"] == model and projected["model_read_decision"] == "read"
    assert projected["reading_policy_flags"] == ["writing_friction"]
    assert path.read_bytes() == before
    assert _review_cache._read_all()["PAPER001"] == entry
    assert propose.project_review(projected) == projected


def test_legacy_cache_does_not_fabricate_a_retained_raw_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "reviews.json")
    entry = {"digest": {"read_decision": "read"}, "quality": None, "goal_summaries": None}
    _review_cache._write_one("PAPER001", entry)
    projected = _review_cache.get_cached_review("PAPER001")
    assert "model_digest" not in projected
    assert projected["model_read_decision"] == "read" and projected["digest"]["read_decision"] == ""
    assert _review_cache._read_all()["PAPER001"] == entry


@pytest.mark.parametrize("ptype", ["position", "theory", "narrative_review", "systematic_review"])
@pytest.mark.parametrize("band", ["highlight", "flag", "uncertain"])
def test_type_specific_html_never_synthesizes_absent_empirical_failures(ptype, band):
    spec = pc.spec_for(ptype)
    rubric = {item.key: "yes" if band == "highlight" else "no" for item in spec.items}
    quality = {**QUALITY, "paper_type": ptype, "quality_band": band, "rubric": rubric,
               "coverage_standard": " / ".join(name for name, _ in spec.standards),
               "coverage_met": len(rubric) if band == "highlight" else 0, "coverage_applicable": len(rubric)}
    rendered = brief.quality_panel_html(quality)
    assert spec.label in rendered
    assert f"/{len(rubric)} applicable" in rendered
    for item in spec.items:
        assert escape(item.question) in rendered
    for invented in ("Ablations isolate", "Validated on independent", "Reports uncertainty", "≥6 grounded"):
        assert invented not in rendered


@pytest.mark.parametrize("rubric", [{}, {"thesis": "na", "evidence_cited": "na"},
                                    {"thesis": "unknown", "evidence_cited": "invalid"}])
def test_no_applicable_coverage_is_unknown_not_a_zero_grade(rubric):
    coverage = pc.coverage_grade(pc.spec_for("position"), rubric, set())
    assert coverage.fraction is None and coverage.grade == "" and coverage.band == "uncertain"
    assert coverage.missing_critical == [] and coverage.applicable == 0
    assert brief._decisive_rows(rubric, "flag")[1] == []


@pytest.mark.parametrize("ptype", [None, "", "unrecognized", "non_paper"])
def test_unknown_type_and_nonpaper_make_no_quality_calls_or_empirical_grade(ptype):
    class NoCalls:
        def pydantic_prompt(self, **_kwargs):
            raise AssertionError("No rubric call is applicable")

    result = quality_eval.evaluate_quality(title="Article", full_text="A proposal.", sections=[],
                                          digest=DIGEST, llm=NoCalls(), max_chars=2000, paper_type=ptype)
    assert result.grade == "" and result.coverage_fraction is None
    assert result.red_flags == [] and result.missing_critical == [] and result.rubric == {}
    assert result.basis == ("non_paper" if ptype == "non_paper" else "not_assessed")


def test_type_failure_retains_diagnostic_without_empirical_fallback():
    class Broken:
        def pydantic_prompt(self, **_kwargs):
            raise RuntimeError("type unavailable")

    import logging
    result = paper_type.detect_safe(SimpleNamespace(title="Article", digest_dump=DIGEST, llm=Broken()),
                                    [], "A proposal.", logging.getLogger(__name__))
    assert result["type"] == "" and result["source"] == "error" and result["uncertain"]
    assert result["reasoning"] == "type unavailable"


def test_standalone_uses_same_policy_and_retains_all_digest_fields():
    source = {**DIGEST, "writing_friction": "high", "writing_reasons": ["§2 uses state before definition."]}
    original = deepcopy(source)
    output = html._render_presentation({"title": "Study"}, source, QUALITY, HIT)
    assert "SKIM" in output and "Idea: high" in output and "Evidence: HIGHLIGHT" in output
    assert "Writing: high" in output and source["writing_reasons"][0] in output
    assert "§2, Fig. 1" in output and "Appendix A" in output and source["original_value"] in output
    assert "Idea preserved; writing friction is high." in output
    assert source == original


def test_nonpaper_standalone_has_no_scientific_grade_gauge_or_failures():
    quality = {**QUALITY, "basis": "non_paper", "quality_band": "", "grade": "", "paper_type": "non_paper"}
    output = html._render_presentation({"title": "Article"}, DIGEST, quality, HIT)
    assert "Evidence: not applicable" in output and "scientific quality not applicable" in output
    assert 'class="gauge"' not in output and "Ablations isolate" not in output


def test_high_friction_is_generated_in_the_existing_digest_call(monkeypatch):
    from zotero_summarizer.services.library import _digest_verification
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    calls = []
    source = {**DIGEST, "writing_friction": "high", "writing_reasons": ["§2 omits a state transition."]}
    model = SimpleNamespace(pydantic_prompt=lambda **kwargs: calls.append(kwargs) or source)
    monkeypatch.setattr(_digest_verification, "verify_digest", lambda *_args, **_kwargs: None)
    result = quality_review.assess_digest(title="Study", full_text="A concept with a missing exposition link.",
                                          config=_default_goals_config(), llm=model)
    assert len(calls) == 1 and calls[0]["pydantic_model"] is PaperDigest
    assert result.writing_reasons == source["writing_reasons"] and result.read_decision == "read"


def test_real_review_worker_keeps_model_output_while_note_and_cache_receive_policy(monkeypatch, tmp_path):
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services.zotero import zotero

    source = PaperDigest(**{**DIGEST, "writing_friction": "high",
                            "writing_reasons": ["§2 omits a state transition."]})
    notes = []
    monkeypatch.setattr(deep_review._map_reduce, "digest_for_strategy", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", lambda _ctx: (QUALITY, HIT, {"type": "position"}, None, None))
    monkeypatch.setattr(zotero, "zotero_upsert_digest_note", lambda key, digest: notes.append((key, digest)))
    path = tmp_path / "fixture.pdf"
    path.write_bytes(b"Synthetic original bytes; extraction is controlled by the fixture")
    reader = SimpleNamespace(get_item_detail=lambda _key: {"title": "Study", "pdf_path": str(path)})
    result = deep_review._review_one({"item_key": "PAPER001"}, reader=reader,
                                    config=_default_goals_config(), extractor=SimpleNamespace(extract_text=lambda _path: "A concept."),
                                    llm=object(), quality_enabled=True)
    assert result["model_digest"] == source.model_dump()
    assert result["model_read_decision"] == "read" and result["digest"]["read_decision"] == "skim"
    assert result["reading_policy_flags"] == ["writing_friction"]
    assert notes[0][0] == "PAPER001" and notes[0][1].read_decision == "skim"
    assert source.read_decision == "read" and result["zotero_note_written"]


def test_fleet_uses_raw_digest_for_proposal_provenance_and_reapplies_the_same_cap(monkeypatch):
    from zotero_summarizer.services.library.review_fleet import fleet, verdict_store

    source = {**DIGEST, "writing_friction": "high", "writing_reasons": ["§2 omits a transition."]}
    review = propose.project_review({"model_digest": source, "digest": source, "quality": QUALITY,
                                     "goal_summaries": HIT})
    saved = []
    monkeypatch.setattr(fleet, "_current_cache", lambda _key: review)
    monkeypatch.setattr(verdict_store, "review_fingerprint", lambda _review: "fixture")
    monkeypatch.setattr(verdict_store, "upsert", lambda key, data: saved.append((key, data)))
    assert fleet._propose_for_item("PAPER001", login={}) == "proposed"
    assert saved[0][0] == "PAPER001"
    assert saved[0][1]["digest_read_decision"] == "read"
    assert saved[0][1]["proposed"] == "should_read"
    assert "writing_friction" in saved[0][1]["flags"]


@pytest.mark.parametrize("module", [pc, propose])
def test_standalone_cache_identity_includes_reused_checklist_and_policy_sources(tmp_path, monkeypatch, module):
    from zotero_summarizer.services.library import paper_render

    source = tmp_path / "fingerprinted_source.py"
    source.write_text("first source revision")
    monkeypatch.setattr(module, "__file__", str(source))
    before = paper_render._compute_renderer_rev()
    source.write_text("second source revision")
    assert paper_render._compute_renderer_rev() != before
