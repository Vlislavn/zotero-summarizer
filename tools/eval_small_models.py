"""Small-model class sweep on a remote endpoint — a clean proxy for local small models.

WHY THIS EXISTS
---------------
The user's local 0.8b/2b models could NOT be measured directly: a parallel 26.5GB
experiment is resident and any local load would contend for RAM (the lesson in
memory `controlled_latency_comparison.md` — never conclude from a swap-contaminated
run). So instead of touching local memory we sweep SMALL models *on the remote
remote endpoint* — they are a SMALLEST-CLASS PROXY of the same family a user would
run locally (gemma-E2B ≈ a local 2b instruct, gemma-E4B ≈ a local 4b, glm-flash ≈
a small fast instruct). The remote reference (GPT-OSS-120B) anchors the top.

WHAT EXTRAPOLATES vs WHAT DOESN'T
---------------------------------
  QUALITY  (digest_completeness, late_recall, format_ok) → extrapolates cleanly.
           A 2b model's quality gap vs a 120b is roughly class-intrinsic, not
           host-intrinsic, so the small-API rank-order is a defensible prior for
           the same-class local rank-order.
  LATENCY  → only the PATTERN extrapolates (cold > warm; on-demand scheduling),
           NOT the absolutes — every remote call carries a network round-trip the
           local model doesn't. We report cold vs warm separately and label the
           absolutes API-bound. Never ratio these against local numbers.

CONTROLLED COMPARISON (the load-bearing discipline)
---------------------------------------------------
Same paper, same prompt, same max_chars budget, warmed (warmups before sampling).
max_tokens is PER-FAMILY (reasoning models get a roomier budget — see below), NOT a
shared invariant: forcing one budget undercounts reasoning models (measured:
GPT-OSS 0.25→1.00 at 2048→8192). One variable: the model. This is the apples-to-apples
discipline that caught the earlier "0.8b 4x faster" error (1-word local vs 80-word API).

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_small_models.py --papers 1
"""
from __future__ import annotations

import argparse
import json
from statistics import mean
from time import perf_counter
from typing import Any

# Candidate "small / adjacent class" remote models, ordered small → reference.
# E2B/E4B = gemma "Edge" 2B/4B (the closest thing the endpoint serves to a local 0.8b-4b);
# 26B-A4B = MoE 26B with 4B active (small-active class); glm-flash = small fast chat;
# qwen3.6-27b = mid; GPT-OSS-120B = the measured reference anchor (2.7s real task).
DEFAULT_MODELS = [
    "gemma-4-E2B-it",
    "gemma-4-E4B-it",
    "glm-4.7-flash",
    "gemma-4-26B-A4B-it",
    "qwen3.6-27b",
    "GPT-OSS-120B",
]
# Per-family max_tokens — NOT one shared budget. A reasoning model (GPT-OSS / qwen3.6)
# spends tokens on an internal thinking phase before emitting the digest, so it needs a
# ROOMIER budget to produce the SAME 7-field output a chat model makes in fewer tokens.
# Forcing one shared budget is a measurement artifact: at max_tokens=2048 GPT-OSS scored
# completeness 0.25 (thinking ate the budget, digest truncated); at 8192 it recovered to
# 1.00 (measured 2026-06-27). The fair invariant is "each model gets ENOUGH budget to
# finish", not "the same number". PROVISIONAL ladder — revisit if a model still truncates.
_CHAT_MAX_TOKENS = 2048       # non-reasoning instruct: 7-field digest fits comfortably
_REASONING_MAX_TOKENS = 8192  # reasoning: thinking phase + the same digest
_DEFAULT_WARMUPS = 1
_DEFAULT_SAMPLES = 1  # digest calls are real-cost; one quality sample + latency profile
_LATENCY_PROBE_PROMPT = "Write one concise 80-word paragraph about reproducibility in machine learning."


def measure_latency(llm: Any, *, prompt: str = _LATENCY_PROBE_PROMPT, warmups: int = 1, samples: int = 3) -> dict[str, Any]:
    """Separate COLD latency (the FIRST call — includes the model LOAD for ollama, or the
    on-demand SCHEDULING for a remote endpoint) from WARM STEADY-STATE (median of ``samples``
    subsequent calls). A wallclock DECISION must use ``warm_median_secs``, NOT a cold number;
    ``cold_start_overhead_secs = cold - warm_median`` is the one-time warm-up cost (report it,
    don't bury it). ``warmups`` (>=1) discarded calls precede sampling — the 1st IS the cold one.
    This is the fix for load-contaminated timings (a cold ollama 8B 'feed call' is mostly load)."""
    from zotero_summarizer.services._common import to_text

    if warmups < 1 or samples < 1:
        raise ValueError("warmups>=1 and samples>=1")
    cold_start = perf_counter()
    to_text(llm.prompt(prompt))
    cold = perf_counter() - cold_start
    for _ in range(warmups - 1):
        to_text(llm.prompt(prompt))
    warm: list[float] = []
    for _ in range(samples):
        start = perf_counter()
        to_text(llm.prompt(prompt))
        warm.append(perf_counter() - start)
    median = sorted(warm)[len(warm) // 2]
    return {"cold_secs": round(cold, 2), "warm_median_secs": round(median, 2),
            "warm_mean_secs": round(mean(warm), 2),
            "cold_start_overhead_secs": round(cold - median, 2), "samples": samples}


def _make_provider(name: str, model: str, base_url: str, api_key_env: str, *,
                   thinking: bool, max_tokens: int):
    """Build an ephemeral openai ProviderConfig pointing at the remote endpoint for `model`."""
    from zotero_summarizer.models.providers import ProviderConfig, ProviderType

    extra_body = {"chat_template_kwargs": {"enable_thinking": thinking}} if thinking else None
    return ProviderConfig(
        name=name,
        type=ProviderType.openai,
        base_url=base_url,
        api_key_env=api_key_env,
        max_tokens=max_tokens,
        temperature=0.0,
        extra_body=extra_body,
    )


def run_one(model: str, title: str, full_text: str, config: Any, *, base_url: str,
            api_key_env: str, max_chars: int, warmups: int, samples: int) -> dict[str, Any]:
    """One model on the fixed paper task → quality + cold/warm latency receipt.

    enable_thinking is set per-family: GPT-OSS / qwen3.6 are reasoning models whose
    digest quality depends on thinking (and which put content in reasoning_content);
    the gemma/glm chat models have no thinking flag and are left plain. A model that
    503s (on-demand cold scheduling) is recorded as unreachable — but ONLY genuine
    endpoint errors (``openai.APIError``: 503/timeout/connection/rate-limit) are
    swallowed-and-recorded; any OTHER exception (a bug, a config error) re-raises so
    the sweep doesn't mask a real problem as 'unreachable'."""
    import openai
    from zotero_summarizer.services.llm.factory import build_client_for_provider
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.setup.calibration import digest_completeness

    is_reasoning = model.startswith(("GPT-OSS", "qwen3", "Qwen3"))
    max_tokens = _REASONING_MAX_TOKENS if is_reasoning else _CHAT_MAX_TOKENS
    provider = _make_provider(f"probe-{model}", model, base_url, api_key_env,
                              thinking=is_reasoning, max_tokens=max_tokens)

    # Latency profile FIRST (cheap probe prompt), so a 503 here fails fast before the
    # expensive digest call. measure_latency already warms then samples.
    llm_probe = build_client_for_provider(provider, model, enable_thinking=False)
    try:
        lat = measure_latency(llm_probe, warmups=warmups, samples=samples)
    except openai.APIError as e:  # 503 on-demand / network — record, don't crash the sweep
        return {"model": model, "reachable": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    # The real task: a full digest on the same paper + budget every other model gets.
    llm_digest = build_client_for_provider(provider, model, enable_thinking=is_reasoning)
    start = perf_counter()
    try:
        digest = assess_digest(title=title, full_text=full_text, config=config,
                               llm=llm_digest, max_chars=max_chars)
    except openai.APIError as e:
        return {"model": model, "reachable": True, "format_ok": False,
                "error": f"digest_api_error: {type(e).__name__}: {str(e)[:120]}", "latency": lat}
    secs = perf_counter() - start
    completeness = digest_completeness(digest)
    # format_ok = the model returned a parseable digest with >0 substantive content
    # (a tiny model that emits garbage JSON or empty fields scores False here).
    format_ok = completeness > 0.0
    return {
        "model": model, "reachable": True, "format_ok": format_ok,
        "completeness": round(completeness, 3), "digest_secs": round(secs, 1),
        "latency": lat, "reasoning": is_reasoning, "max_tokens": max_tokens,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank reachable models by completeness (the extrapolatable axis), with
    format_ok as a hard gate (an unparseable digest is a class failure, not a win)."""
    ok = [r for r in rows if r.get("reachable")]
    if not ok:
        return {"reachable": 0, "note": "no model responded — on-demand endpoint cold?"}
    ranked = sorted(ok, key=lambda r: (r.get("format_ok", False), r.get("completeness", 0.0)), reverse=True)
    return {
        "reachable": len(ok),
        "unreachable": [r["model"] for r in rows if not r.get("reachable")],
        "ranked_by_completeness": [
            {"model": r["model"], "completeness": r.get("completeness"),
             "format_ok": r.get("format_ok"), "warm_median_secs": r.get("latency", {}).get("warm_median_secs"),
             "digest_secs": r.get("digest_secs")}
            for r in ranked
        ],
        "extrapolation_note": (
            "QUALITY rank-order extrapolates to same-class local models; LATENCY absolutes "
            "are API-bound (network round-trip) — only the cold>warm pattern extrapolates."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep small remote models as a local-small-model proxy")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help="remote model ids (small → reference)")
    ap.add_argument("--papers", type=int, default=1, help="cached papers to test on")
    ap.add_argument("--max-chars", type=int, default=None, help="digest text budget (default: config max_text_chars)")
    ap.add_argument("--warmups", type=int, default=_DEFAULT_WARMUPS)
    ap.add_argument("--samples", type=int, default=_DEFAULT_SAMPLES)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.setup.calibration import load_review_papers
    from zotero_summarizer.settings import Settings

    settings = Settings.load()
    config = read_config(settings.config_path, settings.calibration_path)
    max_chars = args.max_chars or config.quality_review.max_text_chars

    base_url = _resolve_base_url()
    api_key_env = "CUSTOM_API_KEY"  # the remote key env name (see .env; secret never read here)

    papers = load_review_papers(settings, limit=args.papers)
    if not papers:
        print("no built paper briefs — open a deep review first."); return 2
    title, full_text = papers[0]
    print(f"paper: {title[:60]!r} | chars={len(full_text)} | budget={max_chars} | "
          f"max_tokens=chat{_CHAT_MAX_TOKENS}/reasoning{_REASONING_MAX_TOKENS} | models={args.models}\n", flush=True)

    rows: list[dict[str, Any]] = []
    for model in args.models:
        r = run_one(model, title, full_text, config, base_url=base_url, api_key_env=api_key_env,
                    max_chars=max_chars, warmups=args.warmups, samples=args.samples)
        if not r.get("reachable"):
            print(f"  {model:22} UNREACHABLE: {r.get('error','')[:80]}", flush=True)
        else:
            lat = r["latency"]
            print(f"  {model:22} complete={r['completeness']:.2f} fmt={r['format_ok']} "
                  f"digest={r['digest_secs']:.0f}s cold={lat['cold_secs']:.1f}s warm={lat['warm_median_secs']:.1f}s "
                  f"reasoning={r['reasoning']}", flush=True)
        rows.append(r)

    summary = summarize(rows)
    print("\n" + json.dumps(summary, indent=2))
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    return 0


def _resolve_base_url() -> str:
    import os
    url = os.environ.get("CUSTOM_BASE_URL")
    if not url:
        raise SystemExit("set CUSTOM_BASE_URL to your remote OpenAI-compatible endpoint")
    return url


if __name__ == "__main__":
    raise SystemExit(main())
