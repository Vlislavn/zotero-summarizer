#!/usr/bin/env python
"""Benchmark a CANDIDATE deep-review model's digest quality against a REFERENCE
(SOTA) model, judged by an independent pinned LLM judge.

Goal: prove a LOCAL model (thinking disabled) reaches SOTA-level digest quality.

Method (firewalled, per the project's grader discipline):
  1. For each built paper (cached ``qa_text``), generate the deep-review DIGEST
     with the reference provider (SOTA, e.g. kather/sota) AND the candidate
     provider (e.g. local ollama/qwen3) at the SAME text budget — isolating model
     quality from tier. Both run with whatever ``extra_body`` their provider
     carries (we disable thinking via config, not here).
  2. Deterministic check FIRST: per-digest field-completeness (fraction of the
     substantive PaperDigest fields that are non-empty) — a cheap, model-free
     quality floor that catches the "thinking-off drops key_findings" failure.
  3. Pinned LLM judge (default Qwen3.5-397B-A17B-FP8 on kather — independent of
     BOTH candidate and reference) scores the two digests PAIRWISE and BLINDED
     (A/B order randomised per paper to cancel position bias) on faithfulness,
     insight and completeness, and picks a winner.
  4. Aggregate: candidate win/tie/loss vs reference, mean judge scores, mean
     field-completeness, per-paper table. The candidate "reaches SOTA level" when
     its loss-rate is low and its mean score ≈ the reference's.

Everything run-specific (providers, models, judge, papers, budget) is a CLI flag;
nothing is hardcoded. Reuses ``quality_review.assess_digest`` (digest gen),
``services.llm.factory.build_client_for_provider`` (clients) and the faithbench
judge constants (pinned model + endpoint).

Usage (from repo root, with .env sourced):
  uv run python tools/bench_deep_review.py \
      --reference-provider kather --reference-model sota \
      --candidate-provider default --candidate-model qwen3:8b \
      --papers 4NIMLFMV,QRPEWC69,R2HRV4JA,YJQWHD6X --max-text-chars 60000
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- repo imports (run via `uv run python tools/...`) -----------------------
from zotero_summarizer.models.providers import ProviderConfig, ProviderType
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.faithbench._constants import (
    DEFAULT_JUDGE_API_KEY_ENV,
    DEFAULT_JUDGE_BASE_URL_ENV,
    DEFAULT_JUDGE_MODEL,
    JUDGE_MAX_TOKENS,
)
from zotero_summarizer.services.library import quality_review
from zotero_summarizer.services.llm.factory import build_client_for_provider
from zotero_summarizer.services.run_log import append_run, file_sha256, make_run_id, short_git_commit
from zotero_summarizer.settings import Settings

# Substantive PaperDigest fields whose emptiness is a real quality signal
# (skip the numeric scores / grade — those are separately judged).
_TEXT_FIELDS = (
    "tldr", "verdict", "read_why", "relevance", "controversies", "impact",
    "unknown_unknowns", "executive_summary", "methods", "limitations",
    "industry_impact", "academy_impact", "key_strength", "key_weakness",
)
_LIST_FIELDS = ("read_parts", "implementation", "key_findings")


def _completeness(digest: dict[str, Any]) -> float:
    """Fraction of substantive fields that are non-empty (0..1)."""
    total = len(_TEXT_FIELDS) + len(_LIST_FIELDS)
    filled = sum(1 for f in _TEXT_FIELDS if str(digest.get(f) or "").strip())
    filled += sum(1 for f in _LIST_FIELDS if [x for x in (digest.get(f) or []) if str(x).strip()])
    return round(filled / total, 3)


_JUDGE_PROMPT = """You are an impartial senior peer reviewer judging the QUALITY of two condensed \
referee DIGESTS of the SAME paper. Each digest should tell a busy researcher what the paper \
contributes, how sound it is, and whether to read it — grounded ONLY in the paper.

You are given the paper text (possibly truncated) and two digests, A and B. Judge each on:
- faithfulness (1-10): every claim grounded in the paper; no invented results/numbers.
- insight (1-10): genuinely useful referee judgment (contribution, evidence, novelty, decision).
- completeness (1-10): the key fields are substantive and specific, not empty or vague.

Be critical and discriminating; do NOT default to a tie. Reward specific, grounded numbers \
and concrete weaknesses; penalise vagueness, empty fields, and any hallucination.

PAPER (may be truncated):
{paper}

DIGEST A:
{digest_a}

DIGEST B:
{digest_b}

Return STRICT JSON only, no prose, no code fences:
{{"a": {{"faithfulness": int, "insight": int, "completeness": int}},
  "b": {{"faithfulness": int, "insight": int, "completeness": int}},
  "winner": "A" | "B" | "tie",
  "reason": "one sentence naming the decisive difference"}}
"""


def _judge_pair(judge_llm: Any, *, paper: str, digest_a: str, digest_b: str, judge_chars: int) -> dict[str, Any]:
    from zotero_summarizer.services._common import extract_json_blob, to_text
    prompt = _JUDGE_PROMPT.format(paper=paper[:judge_chars], digest_a=digest_a, digest_b=digest_b)
    raw = to_text(judge_llm.prompt(prompt))
    return extract_json_blob(raw)


def _digest_for_judge(d: dict[str, Any]) -> str:
    """Compact, field-labelled rendering of a digest for the judge (stable order)."""
    keep = ["tldr", "verdict", "read_decision", "read_why", "executive_summary",
            "key_findings", "methods", "limitations", "key_strength", "key_weakness",
            "controversies", "impact", "unknown_unknowns", "implementation",
            "grade", "soundness", "novelty", "significance", "reproducibility", "clarity"]
    lines = []
    for k in keep:
        v = d.get(k)
        if isinstance(v, list):
            v = "; ".join(str(x) for x in v) if v else "(empty)"
        lines.append(f"{k}: {v if (v is not None and str(v).strip()) else '(empty)'}")
    return "\n".join(lines)


@dataclasses.dataclass
class PaperResult:
    item_key: str
    ref_secs: float
    cand_secs: float
    ref_complete: float
    cand_complete: float
    ref_scores: dict[str, int]
    cand_scores: dict[str, int]
    winner_is_candidate: bool | None  # True=candidate, False=reference, None=tie
    reason: str
    ref_malformed: int = 0            # failed digest-gen attempts (retry signal)
    cand_malformed: int = 0
    judge_raw: dict[str, Any] = dataclasses.field(default_factory=dict)  # full verdict JSON


def _thinking_client(provider: ProviderConfig, model: str, mode: str) -> Any:
    """Build a client whose reasoning is forced ``on``/``off`` (via
    ``chat_template_kwargs.enable_thinking``) or left at the provider default
    (``provider``). Only meaningful for endpoints that honor the flag (ollama
    qwen3, kather sota, vLLM) — exactly the reasoning models this benchmark uses."""
    if mode == "provider":
        return build_client_for_provider(provider, model)
    eb = dict(provider.extra_body or {})
    ctk = dict(eb.get("chat_template_kwargs") or {})
    ctk["enable_thinking"] = (mode == "on")
    eb["chat_template_kwargs"] = ctk
    return build_client_for_provider(provider.model_copy(update={"extra_body": eb}), model)


def _gen_digest(provider: ProviderConfig, model: str, *, title: str, text: str,
                config: Any, max_chars: int, thinking: str = "provider",
                retries: int = 2) -> tuple[dict[str, Any] | None, float, int]:
    """Generate one digest, returning ``(digest|None, secs, malformed_count)``.

    Thinking-off models intermittently emit a structurally invalid digest (e.g. a
    0 where the schema needs 1-5), which surfaces as a parse/validation crash. A
    bounded retry is deliberate benchmark robustness (one bad roll shouldn't void a
    paper) AND a measured quality signal: ``malformed_count`` is the number of
    failed attempts, and a ``None`` digest (all attempts failed) counts as a hard
    quality loss for that model. The retry is narrow and the failure is RECORDED,
    not hidden — this is the authorized benchmark-boundary exception."""
    llm = _thinking_client(provider, model, thinking)
    malformed = 0
    t0 = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            digest = quality_review.assess_digest(
                title=title, full_text=text, config=config, llm=llm, max_chars=max_chars,
            )
            return digest.model_dump(), round(time.perf_counter() - t0, 1), malformed
        except Exception as exc:  # noqa: BLE001 — benchmark survives a flaky model output; recorded
            malformed += 1
            print(f"    malformed digest (attempt {attempt + 1}/{retries + 1}): {type(exc).__name__}: {str(exc)[:80]}", flush=True)
    return None, round(time.perf_counter() - t0, 1), malformed


def _swap_used_mb() -> float | None:
    """macOS swap USED in MB (monotone pressure signal). ``None`` off-darwin."""
    if sys.platform != "darwin":
        return None
    out = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
    # vm.swapusage: total = 5120.00M  used = 4642.81M  free = 477.19M  (encrypted)
    m = re.search(r"used\s*=\s*([\d.]+)M", out)
    return float(m.group(1)) if m else None


def _free_phys_pct() -> float | None:
    """macOS free+inactive physical memory as a % of total (the real headroom
    signal — absolute free swap is misleading because macOS grows the swapfile,
    keeping 'free swap' positive while it thrashes; see the standing memory rule)."""
    if sys.platform != "darwin":
        return None
    total = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip()
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    page = int(re.search(r"page size of (\d+)", vm).group(1)) if re.search(r"page size of (\d+)", vm) else 4096
    free_pages = sum(int(m) for m in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)\.", vm))
    return round(100.0 * (free_pages * page) / int(total), 1) if total else None


# --- persistent results store (mirrors the faithbench run-dir pattern) ------
def _sweep_run_dir(settings: Settings, run_id: str) -> Path:
    return settings.data_dir / "deep_review_sweep" / "runs" / run_id


def _master_log(settings: Settings) -> Path:
    return settings.data_dir / "deep_review_sweep" / "runs-index.jsonl"


def _load_done_keys(run_dir: Path) -> set[str]:
    """Item keys already recorded in papers.jsonl — lets a re-run with the same
    --run-name resume after a memory abort / Ctrl-C instead of re-paying for them."""
    path = run_dir / "papers.jsonl"
    if not path.exists():
        return set()
    return {
        json.loads(line)["item_key"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _append_paper(run_dir: Path, pr: PaperResult) -> None:
    """Append one per-paper result line. Incremental + crash-safe: a memory abort
    mid-sweep keeps every paper completed so far."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "papers.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(dataclasses.asdict(pr), ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference-provider", required=True)
    ap.add_argument("--reference-model", required=True)
    ap.add_argument("--candidate-provider", required=True)
    ap.add_argument("--candidate-model", required=True)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--judge-provider", default=None,
                    help="Provider NAME (from goals.yaml routing) for the judge. Default: the "
                         "pinned faithbench kather endpoint. Set e.g. 'default' for a LOCAL judge "
                         "(ollama) when the cloud budget is exhausted — flag results as indicative.")
    ap.add_argument("--reference-thinking", choices=["on", "off", "provider"], default="provider",
                    help="Force the reference digest's reasoning on/off (or use the provider default).")
    ap.add_argument("--candidate-thinking", choices=["on", "off", "provider"], default="provider",
                    help="Force the candidate digest's reasoning on/off (or use the provider default).")
    ap.add_argument("--papers", required=True, help="Comma-separated built item keys.")
    ap.add_argument("--max-text-chars", type=int, default=60000, help="Default budget for BOTH (isolates model).")
    ap.add_argument("--reference-max-chars", type=int, default=None, help="Override the reference budget (default: --max-text-chars).")
    ap.add_argument("--candidate-max-chars", type=int, default=None, help="Override the candidate budget — e.g. 12000 to match a local LEAN tier (lighter on RAM, production-realistic).")
    ap.add_argument("--judge-chars", type=int, default=24000, help="Paper chars shown to the judge.")
    ap.add_argument("--max-swap-start-mb", type=float, default=6000.0,
                    help="Pre-flight: refuse to START a run that loads a LOCAL model when swap is "
                         "already above this (the box is over-committed; a single model load would "
                         "thrash before the per-paper gate ever checks). free-phys%% reads falsely "
                         "healthy here because it counts reclaimable inactive memory.")
    ap.add_argument("--min-free-phys-pct", type=float, default=6.0,
                    help="Abort before a local gen if free+inactive physical RAM is below this %% (memory safety).")
    ap.add_argument("--max-swap-growth-mb", type=float, default=1500.0,
                    help="Abort if swap-used grew more than this since the last local gen (thrash signal).")
    ap.add_argument("--out", default=None, help="Write full JSON results to this ephemeral path.")
    ap.add_argument("--run-name", default=None,
                    help="Persist a versioned run under data/deep_review_sweep/runs/<ts>_<name>/ "
                         "(manifest.json + papers.jsonl) + a headline in runs-index.jsonl. "
                         "Re-running the SAME run-id resumes (skips completed papers).")
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--seed", type=int, default=0, help="A/B order seed (deterministic blinding).")
    args = ap.parse_args()

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)
    routing = config.llm_routing
    ref_provider = routing.provider_by_name(args.reference_provider)
    cand_provider = routing.provider_by_name(args.candidate_provider)

    # Judge: by default the pinned 397B on the faithbench (kather) endpoint, thinking
    # ON (extra_body=None) for the most careful comparison. ``--judge-provider`` swaps
    # in any routing provider (e.g. a LOCAL ollama judge when the cloud budget is
    # exhausted — independence is then weaker, so flag such runs as indicative).
    if args.judge_provider:
        judge_provider = routing.provider_by_name(args.judge_provider)
    else:
        judge_provider = ProviderConfig(
            name="judge", type=ProviderType.openai,
            base_url=os.environ[DEFAULT_JUDGE_BASE_URL_ENV],
            api_key_env=DEFAULT_JUDGE_API_KEY_ENV, extra_body=None, max_tokens=JUDGE_MAX_TOKENS,
        )
    judge_llm = build_client_for_provider(judge_provider, args.judge_model)

    # Pre-flight memory guard: refuse to START a local run when the box is already
    # over-committed. A big model load can thrash swap before the per-paper gate (which
    # sits BETWEEN the reference and candidate gens) ever runs — exactly how a local
    # reference gen thrashed a 10 GB-swap box once. Absolute swap is the "already loaded"
    # signal; free-phys%% reads falsely healthy (it counts reclaimable inactive memory).
    involves_local = ref_provider.is_local or cand_provider.is_local or judge_provider.is_local
    swap_start = _swap_used_mb()
    if involves_local and swap_start is not None and swap_start > args.max_swap_start_mb:
        print(f"ABORT pre-flight — swap already {swap_start:.0f}MB (> {args.max_swap_start_mb}MB); the box is "
              f"over-committed and loading a local model would thrash. Free RAM / close apps, then re-run "
              f"(the run store resumes automatically).", flush=True)
        return 2

    keys = [k.strip() for k in args.papers.split(",") if k.strip()]
    print(f"reference : {ref_provider.name}/{args.reference_model} @ {ref_provider.base_url} extra_body={ref_provider.extra_body}", flush=True)
    print(f"candidate : {cand_provider.name}/{args.candidate_model} @ {cand_provider.base_url} extra_body={cand_provider.extra_body}", flush=True)
    print(f"judge     : {args.judge_model} @ {judge_provider.base_url} (thinking ON)", flush=True)
    print(f"papers    : {keys} | budget={args.max_text_chars} chars\n", flush=True)

    # Persistent store (optional): a versioned run dir + manifest, written
    # incrementally so a memory abort keeps every completed paper, and resumable.
    run_dir: Path | None = None
    done: set[str] = set()
    if args.run_name:
        run_id = make_run_id(args.run_name)
        run_dir = _sweep_run_dir(settings, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "git_commit": short_git_commit(),
            "goals_yaml_sha": file_sha256(settings.config_path),
            "reference": {"provider": args.reference_provider, "model": args.reference_model,
                          "thinking": args.reference_thinking, "max_chars": args.reference_max_chars or args.max_text_chars},
            "candidate": {"provider": args.candidate_provider, "model": args.candidate_model,
                          "thinking": args.candidate_thinking, "max_chars": args.candidate_max_chars or args.max_text_chars},
            "judge_model": args.judge_model, "judge_base_url": judge_provider.base_url,
            "papers": keys, "seed": args.seed,
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        done = _load_done_keys(run_dir)
        print(f"store     : {run_dir}  (resume: {len(done)} papers already recorded)\n", flush=True)

    results: list[PaperResult] = []

    def record(pr: PaperResult) -> None:
        results.append(pr)
        if run_dir is not None:
            _append_paper(run_dir, pr)

    prev_swap = _swap_used_mb()
    for n, key in enumerate(keys):
        if key in done:
            print(f"[{key}] SKIP — already recorded in the run store (resume)", flush=True)
            continue
        state_path = settings.data_dir / "paper_render" / key / "paper_read.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        qa_text = (state.get("qa_text") or "").strip()
        title = str(state.get("title") or key)
        if len(qa_text) < 2000:
            print(f"[{key}] SKIP — qa_text too short ({len(qa_text)} chars)", flush=True)
            continue

        print(f"[{key}] {title[:60]!r} ({len(qa_text)} chars)", flush=True)
        ref_digest, ref_secs, ref_malformed = _gen_digest(ref_provider, args.reference_model, title=title, text=qa_text, config=config, max_chars=(args.reference_max_chars or args.max_text_chars), thinking=args.reference_thinking)
        print(f"  reference digest in {ref_secs}s (malformed_attempts={ref_malformed}, complete={_completeness(ref_digest) if ref_digest else 'FAILED'})", flush=True)

        # Memory-safety gate BEFORE the (heavy) local gen: free physical RAM % and
        # swap GROWTH since the last gen — never absolute free swap, which stays
        # positive as macOS expands the swapfile under a thrash (the standing rule).
        free_pct = _free_phys_pct()
        swap_now = _swap_used_mb()
        grew = (swap_now - prev_swap) if (swap_now is not None and prev_swap is not None) else 0.0
        if (free_pct is not None and free_pct < args.min_free_phys_pct) or grew > args.max_swap_growth_mb:
            print(f"  ABORT local gen — free_phys={free_pct}% (<{args.min_free_phys_pct}) or swap grew {grew:.0f}MB "
                  f"(>{args.max_swap_growth_mb}); memory safety. Re-run when the box is idle.", flush=True)
            break
        cand_digest, cand_secs, cand_malformed = _gen_digest(cand_provider, args.candidate_model, title=title, text=qa_text, config=config, max_chars=(args.candidate_max_chars or args.max_text_chars), thinking=args.candidate_thinking)
        prev_swap = _swap_used_mb()
        print(f"  candidate digest in {cand_secs}s (malformed_attempts={cand_malformed}, complete={_completeness(cand_digest) if cand_digest else 'FAILED'})", flush=True)

        # A model that can't emit a valid digest at all is a hard quality loss —
        # record it without paying for the judge.
        if ref_digest is None or cand_digest is None:
            winner = None if (ref_digest is None and cand_digest is None) else (False if cand_digest is None else True)
            record(PaperResult(
                item_key=key, ref_secs=ref_secs, cand_secs=cand_secs,
                ref_complete=_completeness(ref_digest) if ref_digest else 0.0,
                cand_complete=_completeness(cand_digest) if cand_digest else 0.0,
                ref_scores={k: 0 for k in ("faithfulness", "insight", "completeness")},
                cand_scores={k: 0 for k in ("faithfulness", "insight", "completeness")},
                winner_is_candidate=winner,
                reason=f"malformed digest (ref_fail={ref_digest is None}, cand_fail={cand_digest is None})",
                ref_malformed=ref_malformed, cand_malformed=cand_malformed,
            ))
            print(f"  → recorded malformed-digest outcome (skipped judge)\n", flush=True)
            continue

        # Blinded A/B: deterministic per-paper order from seed+index.
        cand_is_a = ((args.seed + n) % 2) == 0
        da = _digest_for_judge(cand_digest if cand_is_a else ref_digest)
        db = _digest_for_judge(ref_digest if cand_is_a else cand_digest)
        verdict = _judge_pair(judge_llm, paper=qa_text, digest_a=da, digest_b=db, judge_chars=args.judge_chars)
        wa = str(verdict.get("winner", "tie")).upper()
        if wa == "TIE":
            winner_is_candidate: bool | None = None
        else:
            winner_is_a = (wa == "A")
            winner_is_candidate = (winner_is_a == cand_is_a)
        cand_scores = verdict.get("a" if cand_is_a else "b", {})
        ref_scores = verdict.get("b" if cand_is_a else "a", {})
        reason = str(verdict.get("reason", ""))[:160]
        wlabel = "candidate" if winner_is_candidate else ("reference" if winner_is_candidate is False else "tie")
        print(f"  judge → winner={wlabel} | cand={cand_scores} ref={ref_scores}\n    {reason}\n", flush=True)

        record(PaperResult(
            item_key=key, ref_secs=ref_secs, cand_secs=cand_secs,
            ref_complete=_completeness(ref_digest), cand_complete=_completeness(cand_digest),
            ref_scores={k: int(ref_scores.get(k, 0)) for k in ("faithfulness", "insight", "completeness")},
            cand_scores={k: int(cand_scores.get(k, 0)) for k in ("faithfulness", "insight", "completeness")},
            winner_is_candidate=winner_is_candidate, reason=reason,
            ref_malformed=ref_malformed, cand_malformed=cand_malformed, judge_raw=verdict,
        ))

    if not results:
        print("No results.", flush=True)
        return 1

    n = len(results)
    cand_wins = sum(1 for r in results if r.winner_is_candidate is True)
    ref_wins = sum(1 for r in results if r.winner_is_candidate is False)
    ties = sum(1 for r in results if r.winner_is_candidate is None)
    def _mean(scores_attr: str, dim: str) -> float:
        return round(sum(getattr(r, scores_attr)[dim] for r in results) / n, 2)
    print("=" * 72)
    print(f"BENCHMARK  candidate={args.candidate_provider}/{args.candidate_model}  vs  reference={args.reference_provider}/{args.reference_model}")
    print(f"papers={n}  candidate_wins={cand_wins}  ties={ties}  reference_wins={ref_wins}")
    for dim in ("faithfulness", "insight", "completeness"):
        print(f"  {dim:13s} candidate={_mean('cand_scores', dim):5.2f}  reference={_mean('ref_scores', dim):5.2f}")
    cand_complete = round(sum(r.cand_complete for r in results) / n, 3)
    ref_complete = round(sum(r.ref_complete for r in results) / n, 3)
    cand_total = round(sum(sum(r.cand_scores.values()) for r in results) / n, 2)
    ref_total = round(sum(sum(r.ref_scores.values()) for r in results) / n, 2)
    print(f"  field_completeness candidate={cand_complete}  reference={ref_complete}")
    print(f"  QUALITY /30       candidate={cand_total}  reference={ref_total}  parity={round(cand_total / ref_total, 3) if ref_total else 0}")
    # TIME is a co-equal optimization dimension (not an afterthought): report the
    # digest latency distribution per arm AND a quality-per-minute efficiency, so the
    # quality<->time frontier is visible. Kept SEPARATE from quality (never collapsed
    # into one blended number — see the standing eval rule).
    cs = [r.cand_secs for r in results]
    rs = [r.ref_secs for r in results]
    cand_secs_mean = statistics.mean(cs)
    ref_secs_mean = statistics.mean(rs)
    print(f"  TIME secs (digest) candidate mean={cand_secs_mean:.1f} median={statistics.median(cs):.1f} "
          f"range={min(cs):.0f}-{max(cs):.0f} | reference mean={ref_secs_mean:.1f} median={statistics.median(rs):.1f} "
          f"range={min(rs):.0f}-{max(rs):.0f}")
    cand_qpm = round(cand_total / (cand_secs_mean / 60.0), 2) if cand_secs_mean else 0.0
    ref_qpm = round(ref_total / (ref_secs_mean / 60.0), 2) if ref_secs_mean else 0.0
    print(f"  EFFICIENCY quality-per-min  candidate={cand_qpm}  reference={ref_qpm}  "
          f"(higher = more quality per second of wall-clock)")
    print("=" * 72)

    if args.out:
        Path(args.out).write_text(json.dumps([dataclasses.asdict(r) for r in results], indent=2), encoding="utf-8")
        print(f"wrote {args.out}", flush=True)
    if run_dir is not None:
        cand_q = [sum(r.cand_scores.values()) for r in results]
        ref_q = [sum(r.ref_scores.values()) for r in results]
        headline = {
            "run_id": run_dir.name,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "git_commit": short_git_commit(),
            "reference": f"{args.reference_provider}/{args.reference_model}", "reference_thinking": args.reference_thinking,
            "candidate": f"{args.candidate_provider}/{args.candidate_model}", "candidate_thinking": args.candidate_thinking,
            "candidate_max_chars": args.candidate_max_chars or args.max_text_chars, "judge_model": args.judge_model,
            "n_papers": n, "candidate_wins": cand_wins, "ties": ties, "reference_wins": ref_wins,
            "candidate_quality_mean": cand_total, "candidate_quality_median": round(statistics.median(cand_q), 2),
            "reference_quality_mean": ref_total, "reference_quality_median": round(statistics.median(ref_q), 2),
            "quality_parity": round(cand_total / ref_total, 3) if ref_total else 0,
            "candidate_secs_mean": round(cand_secs_mean, 1), "reference_secs_mean": round(ref_secs_mean, 1),
            "candidate_qpm": cand_qpm, "reference_qpm": ref_qpm,
            "candidate_field_completeness": cand_complete, "reference_field_completeness": ref_complete,
        }
        append_run(_master_log(settings), headline)
        print(f"appended headline → {_master_log(settings)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
