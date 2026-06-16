"""faithbench stats: golden numbers for calculate_statistics — run-level
STD/SEM, Pass@k/Pass^k, tri-state denominators, trap/abstention metrics — and
the single-source-of-truth contract between report.json and report.md."""
from __future__ import annotations

import math

from zotero_summarizer.services.faithbench._report import render_markdown
from zotero_summarizer.services.faithbench._stats import calculate_statistics


def _j(item_id, run_number, success, *, condition="full_text", track="qa",
       failure_reason=None, method="exact", claim_idx=None, extra=None):
    return {
        "item_id": item_id, "track": track, "condition": condition,
        "run_number": run_number, "claim_idx": claim_idx, "success": success,
        "failure_reason": failure_reason, "method": method,
        **({"extra": extra} if extra else {}),
    }


def _r(item_id, run_number, latency, *, condition="full_text", track="qa", status="ok"):
    return {
        "item_id": item_id, "track": track, "condition": condition,
        "run_number": run_number, "status": status, "latency_seconds": latency,
    }


ITEMS_META = {
    "qa:A:0": {"kind": "qa", "answer_type": "number"},
    "qa:A:1": {"kind": "qa", "answer_type": "entity"},
    "trap:A:0": {"kind": "trap", "answer_type": ""},
}


def test_run_level_std_sem_and_pass_k_golden_numbers():
    # 2 runs x 3 items. Run 1: 2/3 correct. Run 2: 1/3 correct.
    judgments = [
        _j("qa:A:0", 1, True), _j("qa:A:1", 1, True),
        _j("trap:A:0", 1, False, failure_reason="hallucinated_on_trap", method="trap_rule"),
        _j("qa:A:0", 2, True), _j("qa:A:1", 2, False, failure_reason="wrong_answer"),
        _j("trap:A:0", 2, False, failure_reason="hallucinated_on_trap", method="trap_rule"),
    ]
    stats = calculate_statistics([], judgments, ITEMS_META)
    block = stats["tracks"]["qa"]["full_text"]

    run_means = [2 / 3, 1 / 3]
    mu = sum(run_means) / 2
    expected_std = math.sqrt(sum((m - mu) ** 2 for m in run_means))  # ddof=1, n=2
    assert block["accuracy"]["mean"] == round(3 / 6, 4)
    assert block["accuracy"]["std_across_runs"] == round(expected_std, 4)
    assert block["accuracy"]["sem_across_runs"] == round(expected_std / math.sqrt(2), 4)
    assert block["accuracy"]["run_means"] == {"1": round(2 / 3, 4), "2": round(1 / 3, 4)}

    # qa:A:0 solved twice (pass^k), qa:A:1 once (pass@k only), trap never
    assert block["k"] == 2
    assert block["pass_at_k"] == round(2 / 3, 4)
    assert block["pass_hat_k"] == round(1 / 3, 4)


def test_single_run_has_zero_std_and_no_pass_k():
    judgments = [_j("qa:A:0", 1, True), _j("qa:A:1", 1, False, failure_reason="wrong_answer")]
    block = calculate_statistics([], judgments, ITEMS_META)["tracks"]["qa"]["full_text"]
    assert block["accuracy"]["std_across_runs"] == 0.0
    assert block["accuracy"]["sem_across_runs"] == 0.0
    assert "pass_at_k" not in block


def test_unjudgeable_rows_leave_every_denominator():
    judgments = [
        _j("qa:A:0", 1, True),
        _j("qa:A:1", 1, None, failure_reason="judge_error", method="llm_judge"),
        _j("trap:A:0", 1, None, failure_reason="harness_fault", method="none"),
    ]
    stats = calculate_statistics([], judgments, ITEMS_META)
    block = stats["tracks"]["qa"]["full_text"]
    assert block["n_validated"] == 1 and block["n_unjudgeable"] == 2
    assert block["n_harness_faults"] == 1
    assert block["accuracy"]["mean"] == 1.0  # 1/1 validated, not 1/3
    assert block["trap"]["n_trap_trials"] == 0  # the harness-faulted trap is excluded
    assert stats["totals"]["n_unjudgeable"] == 2


def test_trap_and_abstention_metrics():
    judgments = [
        # traps: one correct abstention, one hallucination
        _j("trap:A:0", 1, True, method="trap_rule"),
        _j("trap:B:0", 1, False, failure_reason="hallucinated_on_trap", method="trap_rule"),
        # answerable: one correct, one wrong abstain
        _j("qa:A:0", 1, True),
        _j("qa:A:1", 1, False, failure_reason="wrong_abstain", method="abstain_rule"),
    ]
    meta = {**ITEMS_META, "trap:B:0": {"kind": "trap", "answer_type": ""}}
    block = calculate_statistics([], judgments, meta)["tracks"]["qa"]["full_text"]
    assert block["trap"]["hallucination_rate"] == 0.5
    assert block["trap"]["abstention_recall"] == 0.5
    # abstentions: 1 correct (trap) + 1 wrong (answerable) -> precision 0.5
    assert block["trap"]["abstention_precision"] == 0.5
    assert block["wrong_abstain_rate"] == 0.5
    assert block["by_answer_type"]["number"]["accuracy"] == 1.0
    assert block["by_answer_type"]["entity"]["accuracy"] == 0.0


def test_escalation_fraction_latency_and_failure_histogram():
    judgments = [
        _j("qa:A:0", 1, True, method="llm_judge"),
        _j("qa:A:1", 1, False, failure_reason="judge_reject", method="llm_judge"),
        _j("trap:A:0", 1, True, method="trap_rule"),
    ]
    responses = [
        _r("qa:A:0", 1, 10.0), _r("qa:A:1", 1, 30.0), _r("trap:A:0", 1, 20.0),
        _r("qa:A:0", 1, None, status="exception"),  # excluded from latency
    ]
    block = calculate_statistics(responses, judgments, ITEMS_META)["tracks"]["qa"]["full_text"]
    assert block["judge_escalation_fraction"] == round(2 / 3, 4)
    assert block["latency"]["n"] == 3
    assert block["latency"]["p50"] == 20.0
    assert block["latency"]["total_wall_seconds"] == 60.0
    assert block["failure_reasons"] == {"judge_reject": 1}
    assert block["methods"]["llm_judge"] == 2


def test_claims_track_support_rate_and_field_breakdown():
    judgments = [
        _j("claims:P1", 1, True, track="claims", condition="digest",
           method="verbatim", claim_idx=0, extra={"field": "tldr"}),
        _j("claims:P1", 1, False, track="claims", condition="digest",
           failure_reason="unsupported_claim", method="llm_judge", claim_idx=1,
           extra={"field": "key_strength"}),
        _j("claims:P1", 1, None, track="claims", condition="digest",
           failure_reason="judge_error", method="llm_judge", claim_idx=2),
    ]
    block = calculate_statistics([], judgments, {})["tracks"]["claims"]["digest"]
    assert block["n_claims"] == 3 and block["n_validated"] == 2
    assert block["support_rate"]["mean"] == 0.5
    assert block["by_field"]["tldr"]["support_rate"] == 1.0
    assert block["by_field"]["key_strength"]["support_rate"] == 0.0
    assert len(block["top_unsupported"]) == 1


def test_markdown_renders_the_same_numbers_as_json():
    judgments = [
        _j("qa:A:0", 1, True), _j("qa:A:1", 1, False, failure_reason="wrong_answer"),
        _j("trap:A:0", 1, True, method="trap_rule"),
    ]
    stats = calculate_statistics([_r("qa:A:0", 1, 12.0)], judgments, ITEMS_META)
    report = {
        "run_id": "r1", "timestamp": "t", "git_commit": "abc",
        "benchmark_path": "b.jsonl", "benchmark_sha256": "s" * 64,
        "model_under_test": {"model": "m", "base_url": "u"},
        "judge": {"models_used": ["J"]},
        "num_runs": 1, "conditions": ["full_text"], "tracks": ["qa"],
        **stats,
    }
    md = render_markdown(report)
    block = stats["tracks"]["qa"]["full_text"]
    assert f"{100 * block['accuracy']['mean']:.1f}%" in md  # same dict, same number
    assert "Trap hallucination rate" in md and "r1" in md
