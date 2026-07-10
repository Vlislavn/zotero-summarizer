"""A/B harness — does a PROMPT VARIANT beat the shipped baseline on faithfulness?

The shipped triage/digest prompts (``DEFAULT_REFINE_PROMPT`` /
``DEFAULT_TRIAGE_PROMPT`` in ``services/triage/prompts.py``) are faithbench-
validated but were never A/B'd against an alternative. This tool closes that
gap: it COMPARES two already-produced faithbench ``report.json`` files (e.g. the
shipped prompt vs a candidate run on the SAME benchmark) and emits a ship/no-ship
verdict, so a prompt change must BEAT the baseline before it lands.

It does NOT run a benchmark. Producing a fresh variant run is the user's
faithbench responsibility — sweep the candidate prompt, then point this at the
two reports:

    # 1. run baseline + candidate (user, faithbench — same benchmark_vN.jsonl):
    zotero-summarizer faithbench run   --run-id baseline_prompt  ...
    zotero-summarizer faithbench judge --run-id baseline_prompt  ...
    zotero-summarizer faithbench report --run-id baseline_prompt
    #    ...repeat for the candidate (with the alternative prompt wired in)...
    # 2. compare the two reports (this tool, read-only):
    .venv/bin/python tools/eval_prompt_variant.py baseline_prompt candidate_prompt

The two metrics mirror faithbench's own discipline (``services/faithbench/
_stats.py`` is the single source of truth for both numbers):

  * claims ``support_rate``  — fraction of digest claims grounded in the paper
    (``tracks.claims.digest.support_rate.mean``). HIGHER is better.
  * trap ``hallucination_rate`` — fraction of unanswerable "trap" questions the
    model answered anyway (``tracks.qa.<condition>.trap.hallucination_rate``).
    LOWER is better. Aggregated as the WORST (max) over QA conditions — a prompt
    that regresses on any condition's safety is not a win.

A candidate WINS only if support goes UP (by at least ``--support-margin``) AND
hallucination does NOT increase (beyond ``--halluc-tolerance``); a strict
hallucination regression makes the baseline win regardless of support; an
ambiguous move (support within the margin, no regression) is "inconclusive".
We report mean AND median support, the std/SEM across run means, and the pinned
judge model — never trust a single delta without the spread + the judge note.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# A candidate must lift support by MORE than this (absolute, 0-1) to count as an
# improvement; smaller moves are noise → "inconclusive" (named, env-overridable
# via the CLI flags below — no magic number buried in the comparison).
DEFAULT_SUPPORT_MARGIN = 0.01
# A candidate may not raise hallucination by MORE than this; any strict increase
# above the tolerance is a regression that hands the win to the baseline.
DEFAULT_HALLUC_TOLERANCE = 0.0

VERDICT_CANDIDATE = "candidate_better"
VERDICT_BASELINE = "baseline_wins"
VERDICT_INCONCLUSIVE = "inconclusive"


# --- pure metric extraction (faithbench report.json shape) --------------------

def _support_block(report: dict[str, Any]) -> dict[str, Any]:
    """``tracks.claims.digest.support_rate`` — the validated claim support stats
    (mean/median/std/sem across run means). Fail loud if the track is absent."""
    try:
        return report["tracks"]["claims"]["digest"]["support_rate"]
    except (KeyError, TypeError) as exc:
        raise KeyError(
            "report is missing tracks.claims.digest.support_rate — was the "
            "'claims' track run + judged? (faithbench run --tracks qa,claims)"
        ) from exc


def support_rate(report: dict[str, Any]) -> float:
    """Mean claim support_rate (0-1)."""
    return float(_support_block(report)["mean"])


def hallucination_rate(report: dict[str, Any]) -> float:
    """WORST (max) trap hallucination_rate across QA conditions (0-1). A prompt
    that regresses safety on ANY condition (full_text/retrieval) is penalised by
    the worst one. Fail loud if no QA condition carries a trap block."""
    try:
        qa = report["tracks"]["qa"]
    except (KeyError, TypeError) as exc:
        raise KeyError(
            "report is missing tracks.qa — was the 'qa' track run + judged? "
            "(faithbench run --tracks qa,claims)"
        ) from exc
    rates = [
        float(cond["trap"]["hallucination_rate"])
        for cond in qa.values()
        if isinstance(cond, dict) and cond.get("trap") is not None
    ]
    if not rates:
        raise KeyError(
            "no QA condition carries tracks.qa.<condition>.trap.hallucination_rate "
            "— the benchmark has no trap items, so safety cannot be A/B'd"
        )
    return max(rates)


def _judge_models(report: dict[str, Any]) -> list[str]:
    return list((report.get("judge") or {}).get("models_used") or [])


# --- the pure comparison (unit-tested offline, no data) -----------------------

def compare_variants(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    support_margin: float = DEFAULT_SUPPORT_MARGIN,
    halluc_tolerance: float = DEFAULT_HALLUC_TOLERANCE,
) -> dict[str, Any]:
    """Compare two faithbench-style report dicts on faithfulness.

    Returns ``support_rate_delta`` (candidate − baseline, +ve = more grounded),
    ``hallucination_rate_delta`` (candidate − baseline, −ve = safer), and a
    ``verdict``:

      * ``candidate_better`` — support rose by > ``support_margin`` AND
        hallucination did NOT rise by more than ``halluc_tolerance``.
      * ``baseline_wins``   — hallucination rose beyond tolerance (a safety
        regression, even if support also rose), OR support strictly DROPPED by
        more than the margin.
      * ``inconclusive``    — neither: the support move is within the margin and
        there is no safety regression (a wash, not worth shipping).
    """
    base_support = support_rate(baseline)
    cand_support = support_rate(candidate)
    base_halluc = hallucination_rate(baseline)
    cand_halluc = hallucination_rate(candidate)

    support_delta = cand_support - base_support
    halluc_delta = cand_halluc - base_halluc

    halluc_regressed = halluc_delta > halluc_tolerance
    support_improved = support_delta > support_margin
    support_regressed = support_delta < -support_margin

    if halluc_regressed:
        # A safety regression is disqualifying no matter what support did.
        verdict = VERDICT_BASELINE
    elif support_improved:
        # Support up + no safety regression → ship it.
        verdict = VERDICT_CANDIDATE
    elif support_regressed:
        # Support strictly worse (and no safety win to redeem it) → keep baseline.
        verdict = VERDICT_BASELINE
    else:
        verdict = VERDICT_INCONCLUSIVE

    return {
        "support_rate_delta": round(support_delta, 4),
        "hallucination_rate_delta": round(halluc_delta, 4),
        "verdict": verdict,
        "baseline": {"support_rate": round(base_support, 4), "hallucination_rate": round(base_halluc, 4)},
        "candidate": {"support_rate": round(cand_support, 4), "hallucination_rate": round(cand_halluc, 4)},
        "support_margin": support_margin,
        "halluc_tolerance": halluc_tolerance,
    }


# --- read-only report loading + CLI -------------------------------------------

def _resolve_report_path(spec: str, runs_dir: Path) -> Path:
    """A run-id (``runs_dir/<id>/report.json``) or a direct path to report.json."""
    direct = Path(spec).expanduser()
    if direct.is_file():
        return direct
    candidate = runs_dir / spec / "report.json"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"no report.json for {spec!r}: neither {direct} nor {candidate} exists "
        f"(run `faithbench report --run-id {spec}` first?)"
    )


def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _runs_dir(project_root: str | None) -> Path:
    from zotero_summarizer.settings import Settings

    settings = Settings.load(project_root=project_root)
    return settings.faithbench_dir / "runs"


def _spread_line(label: str, block: dict[str, Any]) -> str:
    return (
        f"  {label:<9} support_rate mean={block.get('mean')} "
        f"median={block.get('median')} "
        f"std_across_runs={block.get('std_across_runs')} "
        f"sem_across_runs={block.get('sem_across_runs')}"
    )


def main(argv: list[str] | None = None) -> int:
    from time import perf_counter

    parser = argparse.ArgumentParser(
        description="A/B two faithbench reports (baseline vs prompt-variant candidate). "
                    "Read-only — it COMPARES existing report.json files, it does not run a benchmark.",
    )
    parser.add_argument("baseline", help="Baseline run-id or path to its report.json.")
    parser.add_argument("candidate", help="Candidate (prompt-variant) run-id or path to its report.json.")
    parser.add_argument("--support-margin", type=float, default=DEFAULT_SUPPORT_MARGIN,
                        help=f"Min support_rate lift to count as better (default {DEFAULT_SUPPORT_MARGIN}).")
    parser.add_argument("--halluc-tolerance", type=float, default=DEFAULT_HALLUC_TOLERANCE,
                        help=f"Max tolerated hallucination_rate increase (default {DEFAULT_HALLUC_TOLERANCE}).")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--json", action="store_true", help="Emit the comparison dict as JSON only.")
    args = parser.parse_args(argv)

    started = perf_counter()
    runs_dir = _runs_dir(args.project_root)
    base_path = _resolve_report_path(args.baseline, runs_dir)
    cand_path = _resolve_report_path(args.candidate, runs_dir)
    baseline = _load_report(base_path)
    candidate = _load_report(cand_path)

    result = compare_variants(
        baseline, candidate,
        support_margin=args.support_margin, halluc_tolerance=args.halluc_tolerance,
    )
    elapsed = perf_counter() - started

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    base_judges = _judge_models(baseline)
    cand_judges = _judge_models(candidate)
    print(f"baseline : {base_path}")
    print(f"candidate: {cand_path}")
    print(_spread_line("baseline", _support_block(baseline)))
    print(_spread_line("candidate", _support_block(candidate)))
    print(
        f"  trap hallucination_rate (worst QA condition): "
        f"baseline={result['baseline']['hallucination_rate']} "
        f"candidate={result['candidate']['hallucination_rate']}"
    )
    print(
        f"\nΔ support_rate       = {result['support_rate_delta']:+.4f} "
        f"(margin {args.support_margin}, +ve = more grounded)"
    )
    print(
        f"Δ hallucination_rate = {result['hallucination_rate_delta']:+.4f} "
        f"(tolerance {args.halluc_tolerance}, −ve = safer)"
    )
    print(f"\nVERDICT: {result['verdict']}")
    if base_judges != cand_judges:
        print(
            f"*** JUDGE MISMATCH *** baseline judged by {base_judges or '?'}, "
            f"candidate by {cand_judges or '?'} — the support_rate numbers are NOT "
            f"comparable across different judges; re-judge both with the same pinned judge."
        )
    else:
        print(f"(both judged by {base_judges or '?'}; compared in {elapsed:.3f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
