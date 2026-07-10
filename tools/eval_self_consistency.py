#!/usr/bin/env python
"""Knob-sweep eval for ``quality_review.self_consistency_runs`` (default 3, marked
PROVISIONAL — operational baseline, never independently swept).

WHAT IT MEASURES
----------------
The reference-free deep-review quality eval (``library.quality_eval.evaluate_quality``)
runs the decomposed rubric N=``self_consistency_runs`` times and aggregates the final
{band, grade} by per-run agreement (``band`` collapses to ``"uncertain"`` when the runs
disagree). This tool sweeps N over a small grid (default {1,3,5,7}) on a handful of
CACHED papers and asks the only question that justifies the knob's cost:

  (a) VERDICT STABILITY — for each N, the fraction of papers whose final verdict
      (``band/grade``) is UNCHANGED vs the N=max run, which we treat as the gold
      (more samples = the most settled aggregate). max(N) is gold-by-construction
      → its own stability is 1.0 (a trivial wiring floor, labelled as such).
  (b) LATENCY per N — wall-clock seconds for the rubric at that N (it scales ~linearly
      in calls), so the quality<->cost frontier is visible. Time is a CO-EQUAL
      dimension, never collapsed into the stability number.

GOAL: the SMALLEST N whose stability_vs_max is within ``--tolerance`` of the max — i.e.
is the provisional 3 actually needed, is 1 already fine, or is 5 required? The
recommendation is a standing decision record, not a silent default bump.

DISCIPLINE (mirrors ``tools/bench_deep_review.py`` / ``tools/bench_paper_quality.py``)
  * READ-ONLY on user data: sources papers from ``data/paper_render/<key>/paper_read.json``
    ``qa_text``; writes nothing under ``data/`` (optional ``--out`` is an ephemeral path).
  * MEMORY-SAFE: the same swap/free-phys pre-flight + per-N gate as bench_deep_review,
    applied only when the resolved deep_review client is LOCAL (a remote endpoint skips it).
  * RESOLVED deep_review-stage client: built exactly as production deep-review builds it
    (``resolve_stage(routing,"deep_review")`` → ``build_client_for_stage``) so the sweep
    measures the REAL grader, not a shim.
  * ``perf_counter`` for timing; fail-fast (no bare except / silent fallback).

Usage (from repo root, with .env sourced):
  uv run python tools/eval_self_consistency.py \
      --papers 4NIMLFMV,QRPEWC69,R2HRV4JA --sweep 1,3,5,7 --tolerance 0.1
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

# --- repo imports (run via `uv run python tools/...`) -----------------------
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.library import quality_eval
from zotero_summarizer.services.llm.factory import build_client_for_stage
from zotero_summarizer.settings import Settings

# Minimum qa_text length to be a meaningful rubric input (mirrors bench_deep_review).
_MIN_QA_CHARS = 2000


# --- pure aggregation (unit-tested; no LLM, no I/O) -------------------------

def summarize_consistency_sweep(per_n_verdicts: dict[int, list[str]]) -> list[dict[str, Any]]:
    """Per-N verdict stability vs the max-N run (the gold).

    ``per_n_verdicts`` maps each swept N → the list of per-paper FINAL verdicts
    (one string per paper, e.g. ``"highlight/A"``), in a consistent paper order
    across every N. Returns one row per N (ascending) with::

        {"n": N, "stability_vs_max": frac, "n_papers": P}

    where ``stability_vs_max`` is the fraction of papers whose verdict at N equals
    that paper's verdict at ``max(N)``. The max-N row is gold-by-construction so its
    stability is 1.0 (a trivial floor). Fails fast on a ragged sweep (every N must
    cover the same papers) — a silent length mismatch would fake the stability."""
    if not per_n_verdicts:
        raise ValueError("per_n_verdicts is empty — nothing to summarize")
    n_max = max(per_n_verdicts)
    gold = per_n_verdicts[n_max]
    n_papers = len(gold)
    rows: list[dict[str, Any]] = []
    for n in sorted(per_n_verdicts):
        verdicts = per_n_verdicts[n]
        if len(verdicts) != n_papers:
            raise ValueError(
                f"ragged sweep: N={n} has {len(verdicts)} verdicts but N={n_max} (gold) "
                f"has {n_papers}; every N must cover the same papers"
            )
        if n_papers == 0:
            stability = 0.0
        else:
            matched = sum(1 for v, g in zip(verdicts, gold) if v == g)
            stability = round(matched / n_papers, 4)
        rows.append({"n": n, "stability_vs_max": stability, "n_papers": n_papers})
    return rows


def recommend_smallest_stable_n(rows: list[dict[str, Any]], *, tolerance: float) -> int:
    """The SMALLEST N whose ``stability_vs_max`` is within ``tolerance`` of the max-N
    stability (which is 1.0 by construction). ``tolerance=0`` demands exact gold-match.
    Falls back to max(N) only if nothing qualifies (impossible while the gold row itself
    is always within tolerance, but explicit beats a surprise)."""
    if not rows:
        raise ValueError("no rows to recommend from")
    gold_stability = max(r["stability_vs_max"] for r in rows)
    threshold = gold_stability - tolerance
    qualifying = [r["n"] for r in sorted(rows, key=lambda r: r["n"])
                  if r["stability_vs_max"] >= threshold]
    return qualifying[0] if qualifying else max(r["n"] for r in rows)


# --- memory pre-flight (mirrors bench_deep_review) --------------------------

def _swap_used_mb() -> float | None:
    """macOS swap USED in MB (monotone pressure signal). ``None`` off-darwin."""
    if sys.platform != "darwin":
        return None
    out = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
    m = re.search(r"used\s*=\s*([\d.]+)M", out)
    return float(m.group(1)) if m else None


def _free_phys_pct() -> float | None:
    """macOS free+inactive physical memory as a % of total — the real headroom signal
    (absolute free swap is misleading: macOS grows the swapfile under thrash). ``None``
    off-darwin."""
    if sys.platform != "darwin":
        return None
    total = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip()
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    page = int(re.search(r"page size of (\d+)", vm).group(1)) if re.search(r"page size of (\d+)", vm) else 4096
    free_pages = sum(int(m) for m in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)\.", vm))
    return round(100.0 * (free_pages * page) / int(total), 1) if total else None


# --- paper sourcing (read-only) ---------------------------------------------

def _load_paper(settings: Settings, key: str) -> dict[str, Any] | None:
    """Load one cached paper's ``(title, qa_text)`` from its paper_read.json, or
    ``None`` (with a printed reason) when it's missing / too short to evaluate.
    READ-ONLY — never writes the state back."""
    state_path = settings.data_dir / "paper_render" / key / "paper_read.json"
    if not state_path.exists():
        print(f"[{key}] SKIP — no paper_read.json at {state_path}", flush=True)
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    qa_text = (state.get("qa_text") or "").strip()
    if len(qa_text) < _MIN_QA_CHARS:
        print(f"[{key}] SKIP — qa_text too short ({len(qa_text)} chars < {_MIN_QA_CHARS})", flush=True)
        return None
    return {"key": key, "title": str(state.get("title") or key), "qa_text": qa_text}


def _verdict(quality: Any) -> str:
    """The FINAL verdict whose stability we track: ``band/grade``. ``band`` already
    collapses to ``"uncertain"`` when the self-consistency runs disagree, so this one
    string captures both the headline call and whether the runs settled."""
    return f"{quality.quality_band}/{quality.grade}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--papers", required=True, help="Comma-separated cached item keys (data/paper_render/<key>).")
    ap.add_argument("--sweep", default="1,3,5,7", help="Comma-separated N values to sweep (default 1,3,5,7).")
    ap.add_argument("--tolerance", type=float, default=0.1,
                    help="Stability gap from max(N) the recommended smallest-N may give up (default 0.1).")
    ap.add_argument("--max-chars", type=int, default=12000,
                    help="LLM-fed text cap (default 12000 = the lean tier the local grader runs in production).")
    ap.add_argument("--max-swap-start-mb", type=float, default=6000.0,
                    help="Pre-flight: refuse to START a LOCAL-grader run when swap is already above this "
                         "(box over-committed; a model load would thrash before the per-N gate runs).")
    ap.add_argument("--min-free-phys-pct", type=float, default=6.0,
                    help="Abort before a LOCAL gen when free+inactive physical RAM is below this %% (memory safety).")
    ap.add_argument("--max-swap-growth-mb", type=float, default=1500.0,
                    help="Abort when swap-used grew more than this since the last LOCAL gen (thrash signal).")
    ap.add_argument("--out", default=None, help="Write the full sweep JSON to this EPHEMERAL path (no data/ writes).")
    ap.add_argument("--project-root", default=None)
    args = ap.parse_args()

    sweep = sorted({int(n) for n in args.sweep.split(",") if n.strip()})
    if not sweep:
        print("ABORT — empty --sweep", flush=True)
        return 2
    if any(n < 1 for n in sweep):
        print(f"ABORT — --sweep values must be >= 1 (got {sweep})", flush=True)
        return 2

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    llm = build_client_for_stage(resolved)
    is_local = resolved.provider.is_local

    keys = [k.strip() for k in args.papers.split(",") if k.strip()]
    print(f"grader  : {resolved.provider.name}/{resolved.model} @ {resolved.provider.base_url} "
          f"(local={is_local})", flush=True)
    print(f"sweep   : N in {sweep} | papers={keys} | max_chars={args.max_chars} | tolerance={args.tolerance}\n",
          flush=True)

    # Pre-flight memory guard — only for a LOCAL grader (a remote endpoint adds no host
    # RAM pressure). Refuse to START when the box is already over-committed: a model load
    # would thrash before the per-N gate ever runs. Absolute swap is the "already loaded"
    # signal; free-phys%% reads falsely healthy here (counts reclaimable inactive memory).
    if is_local:
        swap_start = _swap_used_mb()
        if swap_start is not None and swap_start > args.max_swap_start_mb:
            print(f"ABORT pre-flight — swap already {swap_start:.0f}MB (> {args.max_swap_start_mb}MB); the box is "
                  f"over-committed and loading the local grader would thrash. Free RAM, then re-run.", flush=True)
            return 2

    papers = [p for p in (_load_paper(settings, k) for k in keys) if p is not None]
    if not papers:
        print("No usable papers (all missing / too short).", flush=True)
        return 1

    # per_n_verdicts[N] = [verdict per paper, in `papers` order]; per_n_secs[N] = wall-clock.
    per_n_verdicts: dict[int, list[str]] = {n: [] for n in sweep}
    per_n_secs: dict[int, float] = {n: 0.0 for n in sweep}
    prev_swap = _swap_used_mb()

    aborted = False
    for n in sweep:
        if aborted:
            break
        print(f"--- N={n} ---", flush=True)
        for paper in papers:
            # Memory-safety gate BEFORE each LOCAL gen: free physical RAM % + swap GROWTH
            # since the last gen — never absolute free swap (it stays positive under thrash).
            if is_local:
                free_pct = _free_phys_pct()
                swap_now = _swap_used_mb()
                grew = (swap_now - prev_swap) if (swap_now is not None and prev_swap is not None) else 0.0
                if (free_pct is not None and free_pct < args.min_free_phys_pct) or grew > args.max_swap_growth_mb:
                    print(f"  ABORT local gen — free_phys={free_pct}% (<{args.min_free_phys_pct}) or swap grew "
                          f"{grew:.0f}MB (>{args.max_swap_growth_mb}); memory safety. Re-run when the box is idle.",
                          flush=True)
                    aborted = True
                    break
            t0 = perf_counter()
            quality = quality_eval.evaluate_quality(
                title=paper["title"], full_text=paper["qa_text"], sections=[],
                digest={"tldr": paper["qa_text"][:1500], "key_findings": []}, llm=llm,
                max_chars=args.max_chars, self_consistency_runs=n,
            )
            secs = perf_counter() - t0
            if is_local:
                prev_swap = _swap_used_mb()
            verdict = _verdict(quality)
            per_n_verdicts[n].append(verdict)
            per_n_secs[n] += secs
            print(f"  [{paper['key']}] verdict={verdict} "
                  f"(agree {quality.passes_agreed}/{quality.passes_total}) in {secs:.1f}s", flush=True)

    # Only summarize over the Ns fully covered by every paper (a mid-sweep memory abort
    # leaves a partial N out, rather than faking its stability with fewer papers).
    n_evaluated = len(papers)
    complete = {n: v for n, v in per_n_verdicts.items() if len(v) == n_evaluated}
    if len(complete) < 2:
        print("\nNeed at least 2 fully-evaluated N values to compute stability; got "
              f"{sorted(complete)}.", flush=True)
        return 1

    rows = summarize_consistency_sweep(complete)
    recommended = recommend_smallest_stable_n(rows, tolerance=args.tolerance)
    n_max = max(complete)

    print("\n" + "=" * 72)
    print(f"SELF-CONSISTENCY SWEEP  grader={resolved.provider.name}/{resolved.model}  papers={n_evaluated}")
    print(f"gold = N={n_max} run (max samples; its own stability is 1.0 by construction)")
    print(f"{'N':>4} {'stability_vs_max':>18} {'total_secs':>12} {'secs/paper':>12}")
    for row in rows:
        n = row["n"]
        total = per_n_secs[n]
        print(f"{n:>4} {row['stability_vs_max']:>18.3f} {total:>12.1f} "
              f"{(total / n_evaluated if n_evaluated else 0.0):>12.1f}")
    gold_stability = max(r["stability_vs_max"] for r in rows)
    print(f"\nRECOMMENDED smallest-stable N = {recommended}  "
          f"(within {args.tolerance} of max-N stability {gold_stability:.3f}; "
          f"provisional default is 3)")
    print("=" * 72)

    if args.out:
        payload = {
            "grader": f"{resolved.provider.name}/{resolved.model}",
            "papers": [p["key"] for p in papers], "tolerance": args.tolerance,
            "max_chars": args.max_chars, "gold_n": n_max, "recommended_n": recommended,
            "rows": [{**r, "total_secs": round(per_n_secs[r["n"]], 1),
                      "secs_per_paper": round(per_n_secs[r["n"]] / n_evaluated, 1)} for r in rows],
            "per_n_verdicts": complete,
        }
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
