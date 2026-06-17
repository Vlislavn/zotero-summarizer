"""Tests for the paper-type checklists + the pure coverage-grade function.

The standards-fidelity test is the guard the plan calls for: every checklist item
must name a standard and carry a real source URL, so the un-cited / fabricated
empirical-only criteria this module replaces can never silently come back.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.services.library._paper_type_checklists import (
    CHECKLISTS, Coverage, Family, PaperType, coverage_grade, spec_for,
)


def test_every_checklist_item_cites_a_standard_and_source_url():
    for ptype, spec in CHECKLISTS.items():
        assert spec.standards, f"{ptype} has no standards header"
        for name, url in spec.standards:
            assert name and url.startswith("http"), f"{ptype} bad standard {name}/{url}"
        assert spec.items, f"{ptype} has no items"
        for item in spec.items:
            assert item.standard, f"{ptype}:{item.key} missing standard"
            assert item.source_url.startswith("http"), f"{ptype}:{item.key} bad url"
            assert item.question.endswith("?"), f"{ptype}:{item.key} not a yes/no question"


def test_review_types_do_not_carry_empirical_only_items():
    """A review/policy paper must never be judged on ablation/leakage/dataset-split —
    the exact bug this module fixes."""
    empirical_only = {"ablation", "no_leakage", "patient_level_split", "baselines"}
    for ptype in (PaperType.SYSTEMATIC_REVIEW, PaperType.NARRATIVE_REVIEW,
                  PaperType.SURVEY, PaperType.POSITION, PaperType.POLICY):
        keys = {i.key for i in CHECKLISTS[ptype].items}
        assert not (keys & empirical_only), f"{ptype} leaks empirical items {keys & empirical_only}"
        assert Family.EMP not in CHECKLISTS[ptype].families


def test_coverage_excludes_na_items():
    sp = spec_for(PaperType.EMPIRICAL_ML)
    # one item applicable + met, the rest N/A → fraction 1.0 over the single applicable
    rubric = {i.key: "na" for i in sp.items}
    rubric["uncertainty"] = "yes"
    cov = coverage_grade(sp, rubric, {"uncertainty"})
    assert cov.applicable == 1 and cov.met == 1 and cov.fraction == 1.0


def test_met_requires_grounding():
    sp = spec_for(PaperType.NARRATIVE_REVIEW)
    rubric = {i.key: "yes" for i in sp.items}
    # mark all "yes" but ground NONE → nothing counts as met
    cov = coverage_grade(sp, rubric, set())
    assert cov.met == 0 and cov.fraction == 0.0 and cov.band == "flag"


def test_failed_critical_item_caps_band_even_with_high_coverage():
    sp = spec_for(PaperType.CLINICAL_PREDICTION)
    rubric = {i.key: "yes" for i in sp.items}
    rubric["external_validation"] = "no"  # one critical item fails
    grounded = {k for k, v in rubric.items() if v == "yes"}
    cov = coverage_grade(sp, rubric, grounded)
    assert "external_validation" in cov.missing_critical
    assert cov.band in {"flag", "neutral"} and cov.band != "highlight"


def test_red_flag_forces_flag_band():
    sp = spec_for(PaperType.EMPIRICAL_ML)
    rubric = {i.key: "yes" for i in sp.items}
    cov = coverage_grade(sp, rubric, set(rubric), has_red_flag=True)
    assert cov.band == "flag"


def test_review_all_met_is_highlight_a():
    sp = spec_for(PaperType.SYSTEMATIC_REVIEW)
    rubric = {i.key: "yes" for i in sp.items}
    cov = coverage_grade(sp, rubric, set(rubric))
    assert cov.grade == "A" and cov.band == "highlight"


def test_unknown_type_falls_back_to_safe_supertype():
    assert spec_for("not_a_real_type").label.endswith("(type uncertain)")
    assert spec_for(None) is CHECKLISTS[PaperType.GENERIC_EMPIRICAL]


def test_coverage_is_pure_dataclass():
    cov = Coverage(met=1, applicable=2, fraction=0.5, grade="C", band="neutral")
    assert cov.missing_critical == []  # default factory
