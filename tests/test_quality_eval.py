"""Type-aware, checklist-grounded quality eval: structural flags (gated to empirical
types), rubric aggregation, COVERAGE-derived band with self-consistency.

The headline regression: a review paper is judged on SANRA, never flagged for
"no ablation / no leakage discussion" (the bug this redesign fixes)."""
from __future__ import annotations

from zotero_summarizer.services.library import quality_eval
from zotero_summarizer.services.library._quality_prompts import (
    OverstatementLLMResponse,
    RubricLLMResponse,
    SelfVerifyResponse,
)

# The GENERIC_EMPIRICAL checklist keys (default when no paper_type is passed). Each
# value is a >=6-word / >=40-char verbatim span used as grounded evidence.
EV = {
    "baselines": "We compare against strong recent baselines including the current state of the art.",
    "ablation": "We include an extensive ablation study isolating each component contribution here.",
    "uncertainty": "Results are reported with 95% confidence intervals over five random seeds here.",
    "external_validation": "We perform external validation on an independent held-out cohort of data.",
    "no_leakage": "We used a strict held-out test partition with deduplication to prevent contamination.",
    "repro_detail": "The architecture training setup and compute budget are described to reproduce it.",
    "code_data_released": "All code and data are released publicly at github.com/example/repo for reuse.",
    "dataset_provenance": "The dataset provenance is stated with source version and an open license here.",
}
RIGOROUS_BODY = " ".join(EV.values())
ALL_YES = {k: "yes" for k in EV}


class _FakeLLM:
    """Returns scripted rubric samples + a fixed overstatement verdict."""

    def __init__(self, rubric_samples, overstatements=None, evidence=None, reject=None):
        self._samples = list(rubric_samples)
        self._over = overstatements or []
        self._evidence = evidence if evidence is not None else EV
        self._reject = set(reject or [])  # critical keys the self-verify pass rejects
        self._i = 0

    def pydantic_prompt(self, prompt, pydantic_model):
        if pydantic_model is OverstatementLLMResponse:
            return OverstatementLLMResponse(overstatements=self._over)
        if pydantic_model is SelfVerifyResponse:
            return SelfVerifyResponse(verdicts={k: "reject" for k in self._reject})
        sample = self._samples[min(self._i, len(self._samples) - 1)]
        self._i += 1
        return RubricLLMResponse(checks=dict(sample), evidence=dict(self._evidence), concerns=[], band="highlight")


def _eval(llm, *, body=RIGOROUS_BODY, digest=None, paper_type=None, **kw):
    return quality_eval.evaluate_quality(
        title="Hidden", full_text=body, sections=[{"title": "Methods", "text": body}],
        digest=digest or {"tldr": "a method", "key_findings": [], "grade": "A"},
        llm=llm, max_chars=60_000, self_consistency_runs=3, paper_type=paper_type, **kw,
    )


class _CapReporter:
    def __init__(self):
        self.phases = []
        self.subs = []

    def phase(self, name, *, total=0, is_call=False):
        self.phases.append((name, total, is_call))

    def sub(self, done, total):
        self.subs.append((done, total))


def test_evaluate_quality_reports_phase_and_subprogress():
    cap = _CapReporter()
    _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), reporter=cap)
    assert ("quality_rubric", 3, False) in cap.phases
    assert cap.subs == [(1, 3), (2, 3), (3, 3)]
    assert any(name == "quality_overstate" and is_call for name, _t, is_call in cap.phases)


def test_parallel_rubric_preserves_call_count_and_band():
    serial = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), sub_concurrency=1)
    parallel = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), sub_concurrency=3)
    assert parallel.quality_band == serial.quality_band == "highlight"
    assert parallel.passes_total == serial.passes_total == 3


def test_high_rigor_paper_is_highlighted_with_coverage():
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]))
    assert out.quality_band == "highlight" and out.grade == "A"
    assert out.coverage_applicable == len(EV) and out.coverage_met == len(EV)
    assert out.coverage_fraction == 1.0 and out.missing_critical == []
    assert out.paper_type == "generic_empirical" and out.rubric["ablation"] == "yes"
    assert out.overstatements == [] and out.confidence > 0.0


def test_failed_critical_items_flag():
    weak = {**ALL_YES, "external_validation": "no", "uncertainty": "no"}  # two critical fail
    out = _eval(_FakeLLM([weak, weak, weak]))
    assert out.quality_band == "flag"
    assert set(out.missing_critical) >= {"external_validation", "uncertainty"}


def test_leakage_red_flag_trips_flag_on_empirical():
    # A body with a near-perfect number and NO leakage discussion (drop the no_leakage
    # sentence so the structural flag fires).
    body = " ".join(v for k, v in EV.items() if k != "no_leakage") \
        + " The model reached 0.99 AUC on the patient cohort test."
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), body=body)
    assert out.quality_band == "flag"
    assert any("leakage" in f for f in out.red_flags)


def test_review_paper_not_judged_on_empirical_criteria():
    """Headline fix: a review with a cited near-perfect number is scored on SANRA and
    gets NO empirical leakage/ablation red flag."""
    body = ("In this review we survey the field and recommend best practices. "
            "One cited study reported 0.99 AUC on a patient cohort.")
    out = _eval(_FakeLLM([{}, {}, {}]), body=body, paper_type="narrative_review")
    assert not any("leakage" in f for f in out.red_flags)  # empirical flag suppressed
    assert out.coverage_standard.startswith("SANRA")
    assert out.paper_type == "narrative_review"


def test_overstatements_below_threshold_do_not_flag_well_covered_paper():
    # Layer-3 fix: 1-2 abstract-vs-body overstatements (often false-positives on a
    # faithful paper) must NOT alone drag a fully-covered paper to `flag`; only a
    # cluster (>=3) does. Was the bug: a coverage-1.0 case report flagged on 2 overstatements.
    two = _eval(
        _FakeLLM([ALL_YES, ALL_YES, ALL_YES],
                 overstatements=["claims causality without RCT", "SOTA without baseline table"]),
        digest={"tldr": "we prove X causes Y", "key_findings": ["SOTA on everything"], "grade": "B"},
    )
    assert two.quality_band == "highlight" and len(two.overstatements) == 2
    three = _eval(
        _FakeLLM([ALL_YES, ALL_YES, ALL_YES],
                 overstatements=["causal claim, no design", "SOTA, no baseline", "generalizes, no external data"]),
        digest={"tldr": "we prove X causes Y", "key_findings": ["SOTA"], "grade": "B"},
    )
    assert three.quality_band == "flag" and len(three.overstatements) == 3


def test_self_consistency_disagreement_is_uncertain():
    weak = {**ALL_YES, "external_validation": "no", "uncertainty": "no"}  # → flag band
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, weak]))  # 2 highlight, 1 flag → disagree
    assert out.quality_band == "uncertain"


class _PerSampleLLM:
    def __init__(self, evidences):
        self._ev = evidences
        self._i = 0

    def pydantic_prompt(self, prompt, pydantic_model):
        if pydantic_model is OverstatementLLMResponse:
            return OverstatementLLMResponse(overstatements=[])
        ev = self._ev[min(self._i, len(self._ev) - 1)]
        self._i += 1
        return RubricLLMResponse(checks=dict(ALL_YES), evidence=dict(ev), concerns=[], band="highlight")


def test_grounded_evidence_preferred_across_samples():
    # Sample 1 ungrounds two keys; later samples ground them → all met → highlight.
    s1 = {**EV, "baselines": "x", "ablation": "y"}
    out = _eval(_PerSampleLLM([s1, dict(EV), dict(EV)]))
    assert out.quality_band == "highlight" and out.coverage_fraction == 1.0


def test_prestige_no_longer_affects_the_quality_band():
    # Prestige is a RANKING signal now, not a reporting-quality one — the band must be
    # identical whether or not the paper is below the library's prestige floor.
    six = {**ALL_YES, "code_data_released": "no"}  # one non-critical item missing
    uncited = _eval(_FakeLLM([six, six, six]), prestige_known=False)
    below = _eval(_FakeLLM([six, six, six]), prestige_known=True, prestige_score=2.0, prestige_floor=3.0)
    assert uncited.quality_band == below.quality_band


def test_shadow_claim_check_populates_probs_without_changing_the_band(monkeypatch):
    from zotero_summarizer.services.model import claim_checker

    class _FakeChecker:
        def score(self, claims, evidences):
            return [0.91 for _ in claims]

    monkeypatch.setattr(claim_checker, "get_claim_checker", lambda m: _FakeChecker())
    digest = {"tldr": "we report a method", "key_findings": ["52% accuracy"], "grade": "A"}
    off = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), digest=dict(digest))
    on = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), digest=dict(digest),
               shadow_claim_check=True, claim_check_model="flan-t5-large")
    assert off.claim_support_probs == {}
    assert on.quality_band == off.quality_band and on.overstatements == off.overstatements
    assert on.claim_support_probs and all(0.0 <= v <= 1.0 for v in on.claim_support_probs.values())


def test_shadow_off_by_default_does_nothing():
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]))
    assert out.claim_support_probs == {}


def test_near_duplicate_red_flags_are_merged():
    """The 3 self-consistency runs phrase the same concern slightly differently; the
    exact-set dedup left near-duplicates (the user's screenshot bug). They merge now."""
    class _DupLLM:
        def __init__(self):
            self._i = 0

        def pydantic_prompt(self, prompt, pydantic_model):
            if pydantic_model is OverstatementLLMResponse:
                return OverstatementLLMResponse(overstatements=[])
            variants = ["does not report run-to-run determinism of the agent",
                        "does not address run-to-run determinism of the agent",
                        "does not report run-to-run determinism of the agent"]
            c = variants[min(self._i, len(variants) - 1)]
            self._i += 1
            return RubricLLMResponse(checks=dict(ALL_YES), evidence=dict(EV), concerns=[c], band="highlight")

    out = _eval(_DupLLM())
    assert len([f for f in out.red_flags if "determinism" in f]) == 1


# Clinical-prediction fixtures for the self-verification tests. external_validation's
# "quote" is an intentional OVER-CLAIM (vague generalization, no real external cohort).
CLIN_EV = {
    "outcome_defined": "The primary outcome is overall survival at five years for this cohort here.",
    "sample_size": "The development cohort included one thousand two hundred patients with events.",
    "patient_level_split": "We split at the patient level so no patient appears in train and test.",
    "calibration": "We report calibration with a calibration plot and the Brier score for the model.",
    "external_validation": "We claim the model generalizes to external settings and other hospitals broadly.",
    "subgroup_fairness": "Performance is reported across age sex and disease stage subgroups here.",
    "missing_data": "Missing data were handled with multiple imputation across all predictors used.",
    "code_data_released": "All analytical code is released publicly at github.com/example/clin for reuse.",
}
CLIN_BODY = " ".join(CLIN_EV.values())
CLIN_YES = {k: "yes" for k in CLIN_EV}


def _clin_eval(llm, **kw):
    return quality_eval.evaluate_quality(
        title="X", full_text=CLIN_BODY, sections=[{"title": "Methods", "text": CLIN_BODY}],
        digest={"tldr": "a model", "key_findings": [], "grade": "A"}, llm=llm, max_chars=60_000,
        self_consistency_runs=3, paper_type="clinical_prediction", **kw,
    )


def test_self_verification_off_keeps_overclaimed_item_met():
    out = _clin_eval(_FakeLLM([CLIN_YES, CLIN_YES, CLIN_YES], evidence=CLIN_EV), self_verification=False)
    assert "external_validation" not in out.missing_critical
    assert out.self_verification_demoted == []


def test_self_verification_demotes_overclaimed_critical_item():
    out = _clin_eval(
        _FakeLLM([CLIN_YES, CLIN_YES, CLIN_YES], evidence=CLIN_EV, reject={"external_validation"}),
        self_verification=True,
    )
    # The 2nd pass overturned the over-claimed critical → it becomes a missing critical,
    # the band drops, and the overturn is recorded for transparency.
    assert "external_validation" in out.missing_critical
    assert out.self_verification_demoted == ["external_validation"]
    assert out.quality_band != "highlight"


def test_self_verification_only_overturns_explicit_rejects():
    # reject=set() → the verifier confirms everything → nothing demoted.
    out = _clin_eval(_FakeLLM([CLIN_YES, CLIN_YES, CLIN_YES], evidence=CLIN_EV, reject=set()),
                     self_verification=True)
    assert out.self_verification_demoted == []
    assert "external_validation" not in out.missing_critical


def test_clinical_patient_level_phrasing_not_flagged():
    body = "We study patient outcomes with patient-level 5-fold cross-validation on the cohort."
    _, flags = quality_eval._structural([], body)
    assert not any("patient-level split" in f for f in flags)
    plain = "We study patient outcomes with 5-fold cross-validation on the cohort."
    _, flags2 = quality_eval._structural([], plain)
    assert any("patient-level split" in f for f in flags2)


def test_adam_beta_is_not_a_near_perfect_metric():
    """Within-±1 residual fix: a bare near-1 decimal that is an OPTIMIZER hyperparameter
    (Adam β2 = 0.98 / 0.999, as in Transformer/BERT) must NOT trip the near-perfect-metric
    leakage red flag, while a genuine near-perfect performance number still does."""
    hyperparam = "We used the Adam optimizer with β1 = 0.9, β2 = 0.98 and ϵ = 10−9 over 100,000 steps."
    assert not quality_eval._near_perfect_metric(hyperparam)
    _, flags = quality_eval._structural([], hyperparam)
    assert not any("near-perfect" in f for f in flags)
    metric = "Our model reached 0.99 AUC on the held-out test set, a new state of the art."
    assert quality_eval._near_perfect_metric(metric)
    _, flags2 = quality_eval._structural([], metric)
    assert any("near-perfect" in f for f in flags2)


def test_transformer_like_methods_paper_not_capped_to_flag():
    """End-to-end: an A-grade empirical paper whose only near-1 number is its Adam β2 lands
    `neutral`, not `flag` — the Transformer/BERT band the structural false-positive capped."""
    # gold-like rubric: one missing critical (uncertainty), no_leakage N/A for a 2017 paper.
    rubric = {**ALL_YES, "uncertainty": "no", "no_leakage": "na"}
    body = RIGOROUS_BODY + " We trained with the Adam optimizer (β1 = 0.9, β2 = 0.999)."
    out = _eval(_FakeLLM([rubric, rubric, rubric]), body=body)
    assert not any("near-perfect" in f for f in out.red_flags)
    assert out.quality_band == "neutral"
    assert out.grade == "A"
