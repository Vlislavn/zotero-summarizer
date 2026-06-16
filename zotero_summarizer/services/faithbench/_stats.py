"""``calculate_statistics`` — the single source of truth for every number in
both reports (text and JSON read the same dict; ARE ``report_stats`` recipe).

Statistical discipline:
- A trial is *validated* when its judgment is tri-state ``True``/``False``;
  ``None`` (judge error / harness fault) leaves the denominator entirely.
- STD/SEM are computed **across run-level means** (``ddof=1``, guarded to 0.0
  when runs ≤ 1) — pooling correlated repeats would fake-tighten error bars.
- Pass@k (≥1 success) and Pass^k (all-runs success) disambiguate "can it ever"
  from "is it reliable"; only reported when runs > 1.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from zotero_summarizer.services.faithbench._judgment import FailureReason, JudgeMethod


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list[float]) -> float:
    """Median over the validated item set (reported ALONGSIDE the mean so a few
    hard items can't masquerade as a uniformly worse model)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _std_sem_across_runs(per_run: dict[int, list[float]]) -> tuple[float, float, dict[str, float]]:
    """Sample std (ddof=1) + SEM over per-run means; 0.0 when runs ≤ 1."""
    run_means = {run: _mean(vals) for run, vals in sorted(per_run.items()) if vals}
    means = list(run_means.values())
    if len(means) <= 1:
        return 0.0, 0.0, {str(r): round(m, 4) for r, m in run_means.items()}
    mu = _mean(means)
    std = math.sqrt(sum((m - mu) ** 2 for m in means) / (len(means) - 1))
    sem = std / math.sqrt(len(means))
    return std, sem, {str(r): round(m, 4) for r, m in run_means.items()}


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 on empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _latency_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        float(r["latency_seconds"]) for r in rows
        if r.get("status") == "ok" and r.get("latency_seconds") is not None
    ]
    return {
        "n": len(values),
        "p50": round(_percentile(values, 50), 2),
        "p90": round(_percentile(values, 90), 2),
        "p99": round(_percentile(values, 99), 2),
        "mean": round(_mean(values), 2),
        "total_wall_seconds": round(sum(values), 1),
    }


def _qa_condition_stats(
    judgments: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    items_meta: dict[str, dict[str, str]],
) -> dict[str, Any]:
    validated = [j for j in judgments if j.get("success") is not None]
    unjudgeable = [j for j in judgments if j.get("success") is None]
    harness = [j for j in unjudgeable
               if j.get("failure_reason") == FailureReason.HARNESS_FAULT.value]

    def kind_of(j: dict[str, Any]) -> str:
        return items_meta.get(str(j["item_id"]), {}).get("kind", "qa")

    answerable = [j for j in validated if kind_of(j) == "qa"]
    traps = [j for j in validated if kind_of(j) == "trap"]

    per_run: dict[int, list[float]] = defaultdict(list)
    for j in validated:
        per_run[int(j["run_number"])].append(1.0 if j["success"] else 0.0)
    std, sem, run_means = _std_sem_across_runs(per_run)

    per_item: dict[str, list[bool]] = defaultdict(list)
    for j in validated:
        per_item[str(j["item_id"])].append(bool(j["success"]))
    n_runs = len(per_run)
    pass_block: dict[str, Any] = {}
    if n_runs > 1:
        rates = {i: _mean([1.0 if s else 0.0 for s in succ]) for i, succ in per_item.items()}
        pass_block = {
            "k": n_runs,
            "pass_at_k": round(_mean([1.0 if r > 0 else 0.0 for r in rates.values()]), 4),
            "pass_hat_k": round(_mean([1.0 if r == 1.0 else 0.0 for r in rates.values()]), 4),
        }

    trap_passes = sum(1 for j in traps if j["success"])
    hallucinated = sum(
        1 for j in traps
        if j.get("failure_reason") == FailureReason.HALLUCINATED_ON_TRAP.value
    )
    wrong_abstains = sum(
        1 for j in answerable
        if j.get("failure_reason") == FailureReason.WRONG_ABSTAIN.value
    )
    all_abstentions = trap_passes + wrong_abstains  # every abstention, right or wrong

    by_type: dict[str, dict[str, Any]] = {}
    for j in answerable:
        a_type = items_meta.get(str(j["item_id"]), {}).get("answer_type", "span")
        bucket = by_type.setdefault(a_type, {"n": 0, "n_correct": 0})
        bucket["n"] += 1
        bucket["n_correct"] += 1 if j["success"] else 0
    for bucket in by_type.values():
        bucket["accuracy"] = round(bucket["n_correct"] / bucket["n"], 4) if bucket["n"] else 0.0

    escalated = sum(1 for j in validated if j.get("method") == JudgeMethod.LLM_JUDGE.value)

    return {
        "n_items": len({str(j["item_id"]) for j in judgments}),
        "n_trials": len(judgments),
        "n_validated": len(validated),
        "n_unjudgeable": len(unjudgeable),
        "n_harness_faults": len(harness),
        "accuracy": {
            "mean": round(_mean([1.0 if j["success"] else 0.0 for j in validated]), 4),
            "median": round(_median([1.0 if j["success"] else 0.0 for j in validated]), 4),
            "std_across_runs": round(std, 4),
            "sem_across_runs": round(sem, 4),
            "run_means": run_means,
        },
        **pass_block,
        "answerable_accuracy": round(
            _mean([1.0 if j["success"] else 0.0 for j in answerable]), 4
        ),
        "wrong_abstain_rate": round(wrong_abstains / len(answerable), 4) if answerable else 0.0,
        "trap": {
            "n_trap_trials": len(traps),
            "hallucination_rate": round(hallucinated / len(traps), 4) if traps else 0.0,
            "abstention_recall": round(trap_passes / len(traps), 4) if traps else 0.0,
            "abstention_precision": (
                round(trap_passes / all_abstentions, 4) if all_abstentions else 0.0
            ),
        },
        "by_answer_type": by_type,
        "judge_escalation_fraction": (
            round(escalated / len(validated), 4) if validated else 0.0
        ),
        "methods": dict(Counter(str(j.get("method")) for j in judgments)),
        "failure_reasons": dict(
            Counter(str(j["failure_reason"]) for j in judgments if j.get("failure_reason"))
        ),
        "latency": _latency_block(responses),
    }


def _claims_stats(
    judgments: list[dict[str, Any]], responses: list[dict[str, Any]]
) -> dict[str, Any]:
    validated = [j for j in judgments if j.get("success") is not None]
    unjudgeable = [j for j in judgments if j.get("success") is None]

    per_run: dict[int, list[float]] = defaultdict(list)
    for j in validated:
        per_run[int(j["run_number"])].append(1.0 if j["success"] else 0.0)
    std, sem, run_means = _std_sem_across_runs(per_run)

    by_field: dict[str, dict[str, Any]] = {}
    for j in validated:
        field = str((j.get("extra") or {}).get("field") or "unknown")
        bucket = by_field.setdefault(field, {"n": 0, "n_supported": 0})
        bucket["n"] += 1
        bucket["n_supported"] += 1 if j["success"] else 0
    for bucket in by_field.values():
        bucket["support_rate"] = (
            round(bucket["n_supported"] / bucket["n"], 4) if bucket["n"] else 0.0
        )

    unsupported = [
        {"item_id": j["item_id"], "claim_idx": j.get("claim_idx"),
         "details": str(j.get("details") or "")[:300]}
        for j in validated
        if j.get("failure_reason") == FailureReason.UNSUPPORTED_CLAIM.value
    ][:10]

    return {
        "n_claims": len(judgments),
        "n_validated": len(validated),
        "n_unjudgeable": len(unjudgeable),
        "support_rate": {
            "mean": round(_mean([1.0 if j["success"] else 0.0 for j in validated]), 4),
            "median": round(_median([1.0 if j["success"] else 0.0 for j in validated]), 4),
            "std_across_runs": round(std, 4),
            "sem_across_runs": round(sem, 4),
            "run_means": run_means,
        },
        "by_field": by_field,
        "top_unsupported": unsupported,
        "methods": dict(Counter(str(j.get("method")) for j in judgments)),
        "failure_reasons": dict(
            Counter(str(j["failure_reason"]) for j in judgments if j.get("failure_reason"))
        ),
        "latency": _latency_block(responses),
    }


def calculate_statistics(
    responses: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    items_meta: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """All report numbers from the long-form rows. ``items_meta`` maps
    ``item_id`` → ``{kind, answer_type}`` (from the benchmark file)."""
    tracks: dict[str, dict[str, Any]] = {}

    qa_judgments = [j for j in judgments if j.get("track") == "qa"]
    qa_responses = [r for r in responses if r.get("track") == "qa"]
    if qa_judgments:
        tracks["qa"] = {}
        for condition in sorted({str(j["condition"]) for j in qa_judgments}):
            tracks["qa"][condition] = _qa_condition_stats(
                [j for j in qa_judgments if str(j["condition"]) == condition],
                [r for r in qa_responses if str(r["condition"]) == condition],
                items_meta,
            )

    claim_judgments = [j for j in judgments if j.get("track") == "claims"]
    claim_responses = [r for r in responses if r.get("track") == "claims"]
    if claim_judgments:
        tracks["claims"] = {"digest": _claims_stats(claim_judgments, claim_responses)}

    return {
        "totals": {
            "n_response_trials": len(responses),
            "n_judgments": len(judgments),
            "n_validated": sum(1 for j in judgments if j.get("success") is not None),
            "n_unjudgeable": sum(1 for j in judgments if j.get("success") is None),
            "n_harness_faults": sum(
                1 for j in judgments
                if j.get("failure_reason") == FailureReason.HARNESS_FAULT.value
            ),
        },
        "tracks": tracks,
    }
