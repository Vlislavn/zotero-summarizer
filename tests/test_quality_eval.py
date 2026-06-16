"""Reference-free quality eval: structural flags, rubric aggregation, 3-band
verdict with self-consistency + asymmetric prestige floor."""
from __future__ import annotations

from zotero_summarizer.services.library import quality_eval
from zotero_summarizer.services.library._quality_prompts import (
    OverstatementLLMResponse,
    RubricLLMResponse,
)

# Each sentence is a >=6-word / >=40-char verbatim span used as grounded evidence.
EV = {
    "external_validation": "We perform external validation on an independent held-out cohort of patients.",
    "uncertainty": "Results are reported with 95% confidence intervals over five random seeds.",
    "ablation": "We include an extensive ablation study isolating each component contribution.",
    "baselines": "We compare against strong recent baselines including the current state of the art.",
    "dataset_provenance": "The dataset provenance is stated with source version and an open license here.",
    "repro_detail": "The architecture training setup and compute budget are described to reproduce it.",
    "code_data_released": "All code and data are released publicly at github.com/example/repo for reuse.",
}
RIGOROUS_BODY = " ".join(EV.values())
ALL_YES = {k: "yes" for k in EV}


class _FakeLLM:
    """Returns scripted rubric samples + a fixed overstatement verdict."""

    def __init__(self, rubric_samples, overstatements=None):
        self._samples = list(rubric_samples)
        self._over = overstatements or []
        self._i = 0

    def pydantic_prompt(self, prompt, pydantic_model):
        if pydantic_model is OverstatementLLMResponse:
            return OverstatementLLMResponse(overstatements=self._over)
        sample = self._samples[min(self._i, len(self._samples) - 1)]
        self._i += 1
        return RubricLLMResponse(checks=dict(sample), evidence=dict(EV), concerns=[], band="highlight")


def _eval(llm, *, body=RIGOROUS_BODY, digest=None, **kw):
    return quality_eval.evaluate_quality(
        title="Hidden", full_text=body, sections=[{"title": "Methods", "text": body}],
        digest=digest or {"tldr": "a method", "key_findings": [], "grade": "A"},
        llm=llm, max_chars=60_000, self_consistency_runs=3, **kw,
    )


class _CapReporter:
    """Captures ReviewReporter calls so a test can assert phase/sub-progress."""

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
    # rubric phase declares total=runs, then one sub-step per self-consistency pass
    assert ("quality_rubric", 3, False) in cap.phases
    assert cap.subs == [(1, 3), (2, 3), (3, 3)]
    # the overstatement check is its own single-call phase
    assert any(name == "quality_overstate" and is_call for name, _t, is_call in cap.phases)


def test_parallel_rubric_preserves_call_count_and_band():
    # Remote tier (sub_concurrency>1): the self-consistency samples fan out, but the
    # prompt is identical and aggregation is order-deterministic, so the band and
    # sample count match the serial (local) path exactly — speed, not behavior.
    serial = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), sub_concurrency=1)
    parallel = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), sub_concurrency=3)
    assert parallel.quality_band == serial.quality_band == "highlight"
    assert parallel.passes_total == serial.passes_total == 3


def test_parallel_rubric_reports_monotonic_subprogress():
    cap = _CapReporter()
    _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), sub_concurrency=3, reporter=cap)
    # Completion order is nondeterministic under concurrency, but the reported done
    # counts must still cover 1..total exactly, each against the right total.
    assert sorted(d for d, _ in cap.subs) == [1, 2, 3]
    assert all(t == 3 for _, t in cap.subs)


def test_high_rigor_paper_is_highlighted():
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]))
    assert out.quality_band == "highlight"
    assert out.rubric["ablation"] == "yes" and out.evidence["ablation"]
    assert out.overstatements == [] and out.confidence > 0.0


def test_absent_eval_rigor_is_flagged():
    weak = {**ALL_YES, "external_validation": "no", "uncertainty": "no", "ablation": "no"}
    out = _eval(_FakeLLM([weak, weak, weak]))
    assert out.quality_band == "flag"


def test_leakage_red_flag_trips_flag():
    body = RIGOROUS_BODY + " The model reached 0.99 AUC on the patient cohort test."
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, ALL_YES]), body=body)
    assert out.quality_band == "flag"
    assert any("leakage" in f for f in out.red_flags)


def test_overstatement_two_or_more_flags():
    out = _eval(
        _FakeLLM([ALL_YES, ALL_YES, ALL_YES], overstatements=["claims causality without RCT", "SOTA without baseline table"]),
        digest={"tldr": "we prove X causes Y", "key_findings": ["SOTA on everything"], "grade": "B"},
    )
    assert out.quality_band == "flag"
    assert len(out.overstatements) == 2


def test_self_consistency_disagreement_is_uncertain():
    weak = {**ALL_YES, "external_validation": "no", "uncertainty": "no", "ablation": "no"}  # -> flag
    out = _eval(_FakeLLM([ALL_YES, ALL_YES, weak]))  # 2 highlight, 1 flag → disagree
    assert out.quality_band == "uncertain"


class _PerSampleLLM:
    """Different evidence per self-consistency sample (for the grounded-pick test)."""

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
    # Sample 1 has ungrounded quotes for two keys; later samples ground them.
    s1 = {**EV, "baselines": "x", "ablation": "y"}
    out = _eval(_PerSampleLLM([s1, dict(EV), dict(EV)]))
    # The old first-sample-wins bug would drop baselines+ablation from grounded_yes
    # (yes_grounded=5 → neutral); preferring a grounded quote keeps all 7 → highlight.
    assert out.quality_band == "highlight"


def test_shadow_claim_check_populates_probs_without_changing_the_band(monkeypatch):
    # Phase A: the encoder runs ALONGSIDE the LLM and only records support probs;
    # the band/overstatements stay LLM-decided (no behavior change).
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


def test_clinical_patient_level_phrasing_not_flagged():
    body = "We study patient outcomes with patient-level 5-fold cross-validation on the cohort."
    _, flags = quality_eval._structural([], body)
    assert not any("patient-level split" in f for f in flags)
    plain = "We study patient outcomes with 5-fold cross-validation on the cohort."
    _, flags2 = quality_eval._structural([], plain)
    assert any("patient-level split" in f for f in flags2)


def test_prestige_floor_below_median_needs_full_coverage():
    # 6/7 grounded yes (baselines=no): uncited paper → highlight; below-floor known → neutral.
    six = {**ALL_YES, "baselines": "no"}
    uncited = _eval(_FakeLLM([six, six, six]), prestige_known=False)
    assert uncited.quality_band == "highlight"
    below = _eval(_FakeLLM([six, six, six]), prestige_known=True, prestige_score=2.0, prestige_floor=3.0)
    assert below.quality_band == "neutral"  # never demoted below neutral, but must earn highlight
