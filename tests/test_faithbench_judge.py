"""faithbench judge: hard-before-soft ladder, tri-state verdicts, escalation
discipline (the LLM judge sees ONLY the residual band — never traps, never
abstentions, never exact/numeric/containment passes)."""
from __future__ import annotations

import json

import pytest

from zotero_summarizer.services.faithbench._corpus import (
    PaperChunkIndex,
    freeze_paper_text,
    normalize_text,
)
from zotero_summarizer.services.faithbench._dataset import (
    BenchmarkMeta,
    PaperManifestEntry,
    QAItem,
    TrapItem,
)
from zotero_summarizer.services.faithbench._judge import (
    hard_qa_judgment,
    judge_claim,
    judge_equivalence,
    judge_run,
    parse_number,
)
from zotero_summarizer.services.faithbench._judgment import (
    FailureReason,
    JudgeMethod,
    Judgment,
)
from zotero_summarizer.services.faithbench._runner import RunPaths

PAPER_TEXT = (
    "We trained on the ImageNet dataset using 1,281,167 images. "
    "The top-1 accuracy was 85.3 percent after 90 epochs. "
    "Our method is called GlassNet and builds on residual connections."
)


def _qa(**overrides) -> QAItem:
    base = dict(
        item_id="qa:P1:0", paper_item_key="P1", paper_title="T",
        paper_text_sha256="irrelevant", question="Which dataset was used for training?",
        gold_answer="ImageNet", span_start=18, span_end=26, answer_type="entity",
        evidence_sentence="We trained on the ImageNet dataset using 1,281,167 images.",
    )
    base.update(overrides)
    return QAItem(**base)


def _trap(**overrides) -> TrapItem:
    base = dict(
        item_id="trap:P1:0", paper_item_key="P1", paper_title="T",
        paper_text_sha256="irrelevant", question="What was the cohort size?",
        source_paper_item_key="P2", source_gold_answer="412 patients",
    )
    base.update(overrides)
    return TrapItem(**base)


def _row(answer, *, abstained=None, status="ok", error=None):
    parsed = None if answer == "MALFORMED" else {
        "answer": answer,
        "abstained": (answer is None) if abstained is None else abstained,
        "quote": None,
    }
    return {
        "run_id": "r", "item_id": "qa:P1:0", "track": "qa", "condition": "full_text",
        "run_number": 1, "status": status, "error": error, "parsed": parsed,
        "latency_seconds": 1.0,
    }


class FakeJudge:
    """Scripted judge endpoint; records every prompt() call."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.prompts: list[str] = []

    def prompt(self, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)


# ---------------------------------------------------------------------------
# Hard ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["ImageNet", "imagenet", "the ImageNet", "  IMAGENET "])
def test_normalized_exact_match_passes_without_judge(answer):
    verdict = hard_qa_judgment(_qa(), _row(answer))
    assert verdict is not None and verdict.success is True
    assert verdict.method == JudgeMethod.EXACT


def test_numeric_tolerance_and_integer_strictness():
    qa = _qa(gold_answer="85.3", answer_type="number")
    assert hard_qa_judgment(qa, _row("85.3")).success is True
    close = hard_qa_judgment(qa, _row("85.0 percent"))
    assert close.success is True and close.method == JudgeMethod.NUMERIC  # within 1%
    far = hard_qa_judgment(qa, _row("12.0"))
    assert far.success is False and far.failure_reason == FailureReason.WRONG_ANSWER

    int_qa = _qa(gold_answer="1,281,167", answer_type="number")
    assert hard_qa_judgment(int_qa, _row("1281167 images")).success is True
    off_by_one = hard_qa_judgment(int_qa, _row("1281168"))
    assert off_by_one.success is False  # integers never get tolerance


def test_containment_passes_but_long_answers_escalate():
    contained = hard_qa_judgment(_qa(), _row("the ImageNet dataset"))
    assert contained.success is True and contained.method == JudgeMethod.CONTAINMENT
    dump = "ImageNet " + "x" * 400  # anti-gaming cap: paper-dump answers escalate
    assert hard_qa_judgment(_qa(), _row(dump)) is None


def test_trap_rule_is_deterministic_and_never_escalates():
    hallucinated = hard_qa_judgment(_trap(), _row("412 patients"))
    assert hallucinated.success is False
    assert hallucinated.failure_reason == FailureReason.HALLUCINATED_ON_TRAP
    abstained = hard_qa_judgment(_trap(), _row(None))
    assert abstained.success is True and abstained.method == JudgeMethod.TRAP_RULE


def test_wrong_abstain_model_error_and_malformed():
    abstain = hard_qa_judgment(_qa(), _row(None))
    assert abstain.failure_reason == FailureReason.WRONG_ABSTAIN
    err = hard_qa_judgment(_qa(), _row(None, status="exception", error="boom"))
    assert err.failure_reason == FailureReason.MODEL_ERROR and "boom" in err.details
    malformed = hard_qa_judgment(_qa(), _row("MALFORMED"))
    assert malformed.failure_reason == FailureReason.MALFORMED_RESPONSE


def test_residual_band_returns_none_for_escalation():
    assert hard_qa_judgment(_qa(), _row("the large-scale vision benchmark")) is None


def test_parse_number():
    assert parse_number("about 1,281,167 images") == 1281167
    assert parse_number("85.3%") == 85.3
    assert parse_number("no digits") is None


# ---------------------------------------------------------------------------
# Soft judges
# ---------------------------------------------------------------------------


def test_judge_equivalence_verdicts_and_error_tri_state():
    yes = judge_equivalence(FakeJudge([{"equivalent": True, "reason": "same"}]),
                            item=_qa(), answer="the ILSVRC ImageNet set", judge_model="J")
    assert yes.success is True and yes.method == JudgeMethod.LLM_JUDGE and yes.judge_model == "J"

    no = judge_equivalence(FakeJudge([{"equivalent": False, "reason": "different dataset"}]),
                           item=_qa(), answer="CIFAR-10", judge_model="J")
    assert no.success is False and no.failure_reason == FailureReason.JUDGE_REJECT

    # both the call and its strict-JSON retry fail -> unjudgeable, NOT False
    broken = judge_equivalence(FakeJudge([RuntimeError("down"), RuntimeError("down")]),
                               item=_qa(), answer="x", judge_model="J")
    assert broken.success is None and broken.failure_reason == FailureReason.JUDGE_ERROR


def test_judge_claim_verbatim_skips_judge_and_nei_gets_full_text_pass():
    index = PaperChunkIndex(PAPER_TEXT)
    judge = FakeJudge([])
    verbatim = judge_claim(
        judge, claim="The top-1 accuracy was 85.3 percent",
        paper_text=PAPER_TEXT, norm_paper=normalize_text(PAPER_TEXT),
        index=index, max_chars=60_000, judge_model="J",
    )
    assert verbatim.success is True and verbatim.method == JudgeMethod.VERBATIM
    assert judge.calls == 0

    nei_then_yes = FakeJudge([
        {"verdict": "not_enough_info", "evidence": ""},
        {"verdict": "supported", "evidence": "builds on residual connections"},
    ])
    supported = judge_claim(
        nei_then_yes, claim="GlassNet uses residual connections",
        paper_text=PAPER_TEXT, norm_paper=normalize_text(PAPER_TEXT),
        index=index, max_chars=60_000, judge_model="J",
    )
    assert supported.success is True and nei_then_yes.calls == 2

    unsupported = judge_claim(
        FakeJudge([{"verdict": "unsupported", "evidence": ""}]),
        claim="GlassNet was trained on 12 GPUs",
        paper_text=PAPER_TEXT, norm_paper=normalize_text(PAPER_TEXT),
        index=index, max_chars=60_000, judge_model="J",
    )
    assert unsupported.failure_reason == FailureReason.UNSUPPORTED_CLAIM


def test_read_why_claim_is_judged_against_paper_plus_goals():
    goals = "Agent autonomy, determinism, and glassboxing; Multiagent systems"
    judge = FakeJudge([{"verdict": "supported", "evidence": "builds on residual connections"}])
    verdict = judge_claim(
        judge, claim="GlassNet addresses agent autonomy in vision pipelines",
        paper_text=PAPER_TEXT, norm_paper=normalize_text(PAPER_TEXT),
        index=PaperChunkIndex(PAPER_TEXT), max_chars=60_000, judge_model="J",
        field="read_why", research_goals=goals,
    )
    assert verdict.success is True
    assert verdict.extra == {"judged_against": "paper+goals"}
    assert "research goals" in judge.prompts[0] and goals in judge.prompts[0]


@pytest.mark.parametrize("field,goals", [
    ("read_why", ""),       # no goals available -> paper-only standard
    ("tldr", "Agent autonomy"),  # goal-conditioned standard is read_why-only
])
def test_other_claims_keep_the_paper_only_standard(field, goals):
    judge = FakeJudge([{"verdict": "unsupported", "evidence": ""}])
    verdict = judge_claim(
        judge, claim="GlassNet was trained on 12 GPUs",
        paper_text=PAPER_TEXT, norm_paper=normalize_text(PAPER_TEXT),
        index=PaperChunkIndex(PAPER_TEXT), max_chars=60_000, judge_model="J",
        field=field, research_goals=goals,
    )
    assert verdict.failure_reason == FailureReason.UNSUPPORTED_CLAIM
    assert "research goals" not in judge.prompts[0]
    assert "judged_against" not in verdict.extra


# ---------------------------------------------------------------------------
# judge_run orchestration
# ---------------------------------------------------------------------------


def _setup_run(tmp_path, *, sha_override=None):
    papers_dir = tmp_path / "papers"
    sha = freeze_paper_text(papers_dir, "P1", PAPER_TEXT)
    meta = BenchmarkMeta(
        version=1, created_at="t", builder_model="B",
        papers=[PaperManifestEntry(item_key="P1", title="T",
                                   text_sha256=sha_override or sha, n_chars=len(PAPER_TEXT))],
    )
    items = [_qa(paper_text_sha256=sha), _trap(paper_text_sha256=sha)]
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    rows = [
        _row("ImageNet"),
        {**_row("412 patients"), "item_id": "trap:P1:0"},
    ]
    with paths.responses.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return meta, items, papers_dir, paths


def test_judge_run_writes_verdicts_and_traps_never_reach_judge(tmp_path):
    meta, items, papers_dir, paths = _setup_run(tmp_path)
    judge = FakeJudge([])
    counts = judge_run(meta=meta, items=items, papers_dir=papers_dir, paths=paths,
                       judge_llm=judge, judge_model="J", max_text_chars=60_000)
    assert counts["judged"] == 2 and counts["escalated"] == 0 and judge.calls == 0
    rows = [json.loads(line) for line in paths.judgments.read_text().splitlines()]
    by_id = {r["item_id"]: r for r in rows}
    assert by_id["qa:P1:0"]["success"] is True
    assert by_id["trap:P1:0"]["failure_reason"] == FailureReason.HALLUCINATED_ON_TRAP.value

    # idempotent: a second pass judges nothing new
    counts2 = judge_run(meta=meta, items=items, papers_dir=papers_dir, paths=paths,
                        judge_llm=judge, judge_model="J", max_text_chars=60_000)
    assert counts2["judged"] == 0 and counts2["skipped"] == 2


def test_judge_run_frozen_text_drift_is_harness_fault_not_model_failure(tmp_path):
    meta, items, papers_dir, paths = _setup_run(tmp_path, sha_override="0" * 64)
    counts = judge_run(meta=meta, items=items, papers_dir=papers_dir, paths=paths,
                       judge_llm=FakeJudge([]), judge_model="J", max_text_chars=60_000)
    rows = [json.loads(line) for line in paths.judgments.read_text().splitlines()]
    assert counts["judged"] == 2
    assert all(r["success"] is None for r in rows)
    assert all(r["failure_reason"] == FailureReason.HARNESS_FAULT.value for r in rows)


def test_judge_run_routes_read_why_claims_through_goal_aware_prompt(tmp_path):
    papers_dir = tmp_path / "papers"
    sha = freeze_paper_text(papers_dir, "P1", PAPER_TEXT)
    meta = BenchmarkMeta(
        version=1, created_at="t", builder_model="B",
        papers=[PaperManifestEntry(item_key="P1", title="T",
                                   text_sha256=sha, n_chars=len(PAPER_TEXT))],
    )
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    row = {
        "run_id": "r", "item_id": "claims:P1", "track": "claims",
        "condition": "digest", "run_number": 1, "status": "ok",
        "parsed": {"claims": [
            {"field": "read_why", "claim": "GlassNet addresses agent autonomy"},
            {"field": "tldr", "claim": "GlassNet was trained on 12 GPUs"},
        ]},
    }
    paths.responses.write_text(json.dumps(row) + "\n", encoding="utf-8")

    judge = FakeJudge([{"verdict": "supported", "evidence": ""},
                       {"verdict": "unsupported", "evidence": ""}])
    counts = judge_run(
        meta=meta, items=[], papers_dir=papers_dir, paths=paths,
        judge_llm=judge, judge_model="J", max_text_chars=60_000,
        research_goals="Agent autonomy, determinism, and glassboxing",
    )
    assert counts["judged"] == 2
    rows = [json.loads(line) for line in paths.judgments.read_text().splitlines()]
    by_idx = {r["claim_idx"]: r for r in rows}
    assert by_idx[0]["success"] is True
    assert by_idx[0]["extra"] == {"judged_against": "paper+goals", "field": "read_why"}
    assert "Agent autonomy" in judge.prompts[0] and "research goals" in judge.prompts[0]
    assert by_idx[1]["success"] is False
    assert by_idx[1]["extra"] == {"field": "tldr"}
    assert "research goals" not in judge.prompts[1]


def test_judge_run_claims_unknown_paper_is_harness_fault_not_crash(tmp_path):
    papers_dir = tmp_path / "papers"
    sha = freeze_paper_text(papers_dir, "P1", PAPER_TEXT)
    meta = BenchmarkMeta(
        version=1, created_at="t", builder_model="B",
        papers=[PaperManifestEntry(item_key="P1", title="T", text_sha256=sha, n_chars=len(PAPER_TEXT))],
    )
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    # A claims row for a paper that is NOT in the manifest (rebuilt/edited
    # benchmark) — previously an uncaught KeyError on texts[paper_key].
    row = {
        "run_id": "r", "item_id": "claims:GHOST", "track": "claims",
        "condition": "digest", "run_number": 1, "status": "ok",
        "parsed": {"claims": [{"field": "tldr", "claim": "anything at all"}]},
    }
    paths.responses.write_text(json.dumps(row) + "\n", encoding="utf-8")

    judge = FakeJudge([])  # must never be called
    counts = judge_run(meta=meta, items=[], papers_dir=papers_dir, paths=paths,
                       judge_llm=judge, judge_model="J", max_text_chars=60_000)
    assert counts["judged"] == 1  # emitted, not crashed
    rows = [json.loads(line) for line in paths.judgments.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["success"] is None
    assert rows[0]["failure_reason"] == FailureReason.HARNESS_FAULT.value
    assert judge.calls == 0


def test_judgment_invariants():
    with pytest.raises(ValueError):
        Judgment(success=False)  # failure must carry a reason
    with pytest.raises(ValueError):
        Judgment(success=None, failure_reason=FailureReason.WRONG_ANSWER)  # not unjudgeable
    with pytest.raises(ValueError):
        Judgment(success=True, failure_reason=FailureReason.WRONG_ANSWER)
