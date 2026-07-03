"""A/B the deep-review chunking strategies on real papers — coverage vs cost.

Strategies (quality_review.chunk_strategy):
  prefix      naive truncate to max_text_chars, one digest call (baseline)
  rank        BM25-select chunks to fit max_text_chars, one digest call (shipped default)
  map_reduce  summarise each chunk on the cheap/LOCAL model, synthesise on the API model

Decision metric is LATE-CONTENT RECALL: distinctive tokens (numbers / named entities) that
appear ONLY in the last third of the paper and also surface in the digest — i.e. did the
strategy actually read the whole paper, not just the start? Plus digest completeness + wall
latency. `prefix` should score low on late_recall; `rank`/`map_reduce` higher.

Memory-safe: prefix/rank run on the API (remote) endpoint; map_reduce's LOCAL map step loads a
model, so it's opt-in (`--include-local`) and swap-pre-flighted + swap-delta-reported (the
memory-safe-runs rule). Run user-coordinated, foreground, single instance.

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_chunking.py --include-local
"""
from __future__ import annotations

import argparse
import json
import re
from time import perf_counter
from typing import Any

# Distinctive = numbers (incl. decimals/percentages) and Capitalized/ALLCAPS terms ≥3 chars.
_NUMBER = re.compile(r"\d[\d.,]*%?")
_NAMED = re.compile(r"\b(?:[A-Z][A-Za-z0-9]{2,}|[A-Z]{2,})\b")
_MAX_SWAP_START_MB = 6000.0  # refuse a LOCAL map when the box is already over-committed


def distinctive_tokens(text: str) -> set[str]:
    """Numbers + named entities (the facts a faithful digest should carry)."""
    body = text or ""
    return {t.rstrip(".,") for t in _NUMBER.findall(body)} | set(_NAMED.findall(body))


def late_recall(digest_text: str, full_text: str, *, tail_frac: float = 0.34) -> float:
    """Fraction of distinctive tokens that occur ONLY in the last ``tail_frac`` of the paper
    (not earlier) AND appear in the digest. Measures whole-paper coverage; deterministic."""
    if not (0.0 < tail_frac < 1.0):
        raise ValueError("tail_frac must be in (0, 1)")
    body = full_text or ""
    cut = int(len(body) * (1.0 - tail_frac))
    late_only = distinctive_tokens(body[cut:]) - distinctive_tokens(body[:cut])
    if not late_only:
        return 0.0
    in_digest = distinctive_tokens(digest_text)
    return len(late_only & in_digest) / len(late_only)


# Below this late_recall spread the strategies are coverage-equivalent on the deterministic
# metric (a digest carries only a handful of tokens, so absolute recall is tiny + noisy) →
# decide on COST (latency). Above it, the higher-coverage strategy wins.
_RECALL_TOLERANCE = 0.05


def summarize_ab(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per (strategy, chunk_chars) mean late_recall / completeness / secs. Winner = highest
    late_recall, but when the spread is within ``_RECALL_TOLERANCE`` (coverage-equivalent, the
    common case for a compressing digest) the FASTEST arm wins. ``inconclusive`` flags the
    tolerance case so the caller doesn't over-read a noise-level coverage difference. Grouping by
    chunk_chars too means a map_reduce SIZE SWEEP keeps each size as its own arm (not averaged)."""
    if not results:
        raise ValueError("summarize_ab needs at least one result")
    by_arm: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in results:
        key = (r["strategy"], r.get("chunk_chars", 0))
        by_arm.setdefault(key, []).append(r)
    rows = []
    for (strategy, chars), rs in by_arm.items():
        n = len(rs)
        rows.append({
            "strategy": strategy, "chunk_chars": chars, "n": n,
            "late_recall": round(sum(r["late_recall"] for r in rs) / n, 3),
            "completeness": round(sum(r["completeness"] for r in rs) / n, 3),
            "secs": round(sum(r["secs"] for r in rs) / n, 1),
        })
    best = max(row["late_recall"] for row in rows)
    eligible = [row for row in rows if row["late_recall"] >= best - _RECALL_TOLERANCE]
    inconclusive = len({(row["strategy"], row["chunk_chars"]) for row in eligible}) > 1
    winner = min(eligible, key=lambda row: row["secs"])  # coverage-equivalent → cheapest
    return {"rows": rows, "winner": winner["strategy"], "winner_chunk_chars": winner["chunk_chars"],
            "inconclusive_coverage": inconclusive, "decided_by": "latency" if inconclusive else "late_recall"}


def _digest_text(digest: Any) -> str:
    return " ".join(str(v) for v in digest.model_dump().values() if isinstance(v, (str, int, float)))


def run_strategy(strategy: str, title: str, full_text: str, config: Any, *,
                 reduce_llm: Any, map_llm: Any, chunk_chars: int, sub_concurrency: int,
                 return_digest: bool = False) -> dict[str, Any]:
    """Run ONE strategy on ONE paper → {strategy, secs, late_recall, completeness}.

    ``return_digest`` adds the joined digest text (for the faithfulness judge)."""
    from zotero_summarizer.services.library._map_reduce import map_reduce_digest
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.setup.calibration import digest_completeness

    budget = int(config.quality_review.max_text_chars)
    start = perf_counter()
    if strategy == "prefix":
        digest = assess_digest(title=title, full_text=full_text[:budget], config=config, llm=reduce_llm, max_chars=budget)
    elif strategy == "rank":
        digest = assess_digest(title=title, full_text=full_text, config=config, llm=reduce_llm, max_chars=budget)
    elif strategy == "map_reduce":
        digest = map_reduce_digest(title=title, full_text=full_text, config=config, map_llm=map_llm,
                                   reduce_llm=reduce_llm, chunk_chars=chunk_chars, sub_concurrency=sub_concurrency)
    else:
        raise ValueError(f"unknown strategy {strategy!r}")
    secs = perf_counter() - start
    out = {"strategy": strategy, "secs": secs,
           "late_recall": late_recall(_digest_text(digest), full_text),
           "completeness": digest_completeness(digest)}
    if return_digest:
        out["digest_text"] = _digest_text(digest)
    return out


# Max source chars given to the faithfulness judge in one call. A full paper (60–160k chars)
# won't fit a small judge's window; we sample a prefix + a late tail so late-paper claims are
# also checkable (mirrors late_recall's whole-paper intent). Fair across strategies (same window).
_JUDGE_SOURCE_BUDGET = 24_000
_JUDGE_SYS = (
    "You are a STRICT faithfulness grader. Given a paper's source text and a digest, count the "
    "digest's factual claims that are SUPPORTED by the source vs UNSUPPORTED (hallucinated or "
    "contradicted). A claim is SUPPORTED only if the source contains evidence for it. Be strict: "
    "a plausible-but-absent claim is UNSUPPORTED. Reply with compact JSON only."
)


def judge_faithfulness(digest_text: str, full_text: str, *, judge_llm: Any, judge_model: str,
                       max_tokens: int = 1024) -> dict[str, Any]:
    """LLM judge: digest claims grounded in the source? → {supported, unsupported, score, note}.

    ``score = supported / (supported + unsupported)`` (1.0 = fully grounded). I/O BOUNDARY: the
    judge is a free-text LLM — its JSON may be malformed; a narrow ``JSONDecodeError`` catch
    records the raw text (surfaced) rather than masking, and scores None (undecidable), matching
    the g10 judge + global grader discipline (mark undecidable, never fabricate a score)."""
    import json as _json
    src = full_text or ""
    half = _JUDGE_SOURCE_BUDGET // 2
    window = (src[:half] + "\n…[middle elided]…\n" + src[len(src) // 2: len(src) // 2 + half]) if len(src) > _JUDGE_SOURCE_BUDGET else src
    user = (
        f"SOURCE TEXT (paper):\n{window}\n\n"
        f"DIGEST (claims to grade):\n{digest_text}\n\n"
        f'Count SUPPORTED vs UNSUPPORTED digest claims. Output JSON ONLY: '
        f'{{"supported": <int>, "unsupported": <int>, "note": "<one short line>"}}'
    )
    body = _json.dumps({"model": judge_model, "temperature": 0, "max_tokens": max_tokens,
                        "messages": [{"role": "system", "content": _JUDGE_SYS},
                                     {"role": "user", "content": user}]}).encode()
    import os, time, urllib.request
    base = os.environ["CUSTOM_BASE_URL"]; key = os.environ["CUSTOM_API_KEY"]
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as resp:
        d = _json.loads(resp.read())
    secs = round(time.time() - t0, 1)
    msg = d["choices"][0]["message"]
    # Reasoning models (e.g. GPT-OSS-120B): the thinking phase can eat the WHOLE max_tokens
    # budget → ``content`` is None/"" and the answer lives in ``reasoning_content``. I/O BOUNDARY:
    # an empty content is an undecidable judge call (not a crash) — read reasoning_content as the
    # fallback; if both are empty, score None (surfaced), never fabricated.
    txt = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0:
        return {"supported": None, "unsupported": None, "score": None, "note": txt[:160], "secs": secs, "judge": judge_model}
    try:
        j = _json.loads(txt[s:e + 1])
    except _json.JSONDecodeError:
        return {"supported": None, "unsupported": None, "score": None, "note": txt[:160], "secs": secs, "judge": judge_model}
    sup = int(j.get("supported") or 0); unsup = int(j.get("unsupported") or 0)
    tot = sup + unsup
    score = round(sup / tot, 3) if tot else None
    return {"supported": sup, "unsupported": unsup, "score": score,
            "note": (j.get("note") or "")[:160], "secs": secs, "judge": judge_model}


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B deep-review chunking strategies (coverage vs cost)")
    ap.add_argument("--papers", type=int, default=2, help="How many cached papers to A/B")
    ap.add_argument("--chunk-chars", default="8000",
                    help="map_reduce chunk size(s) — int OR comma list to SWEEP (e.g. 4000,8000,16000)")
    ap.add_argument("--map-model", default="qwen3.5:0.8b", help="LOCAL model for the map step (small = memory-safe)")
    ap.add_argument("--map-provider", default=None,
                    help="Provider for the map step (default: the local provider). Pass the API "
                         "provider to A/B the map_reduce ALGORITHM memory-safely (no local load).")
    ap.add_argument("--include-local", action="store_true",
                    help="Allow map_reduce when its map step is LOCAL (loads a model). Swap-pre-flighted.")
    ap.add_argument("--out", default=None)
    ap.add_argument("--faithfulness", action="store_true",
                    help="ALSO run an LLM faithfulness judge (digest claims vs source) per strategy. "
                         "Closes GAP §G8 (the real quality measure the coverage metric can't be).")
    ap.add_argument("--judge-model", default="GPT-OSS-120B",
                    help="Faithfulness judge model on the CUSTOM_BASE_URL endpoint. Default GPT-OSS-120B.")
    ap.add_argument("--judge-max-tokens", type=int, default=8192,
                    help="Judge max_tokens (reasoning models need room for thinking+output; 1024 "
                         "left content=None on some calls). Default 8192.")
    args = ap.parse_args()
    # Parse chunk-chars as a sweep list (single int → one-element list). A sweep A/Bs which
    # chunk SIZE wins on coverage/cost — the dimension `map_chunk_chars=8000` was a GUESS on.
    chunk_sizes = [int(x.strip()) for x in args.chunk_chars.split(",") if x.strip()]
    if not chunk_sizes:
        raise ValueError("--chunk-chars must be one or more positive integers")

    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.llm.factory import build_client_for_provider
    from zotero_summarizer.services.setup.calibration import load_review_papers, swap_used_mb
    from zotero_summarizer.settings import Settings

    settings = Settings.load()
    config = read_config(settings.config_path, settings.calibration_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    reduce_llm = build_client_for_provider(resolved.provider, resolved.model, enable_thinking=True)

    # Map provider: explicit, else the local provider. A LOCAL map loads a model (memory-gated);
    # an API map provider runs the map_reduce ALGORITHM with no local load (coverage A/B).
    default_local = next((p.name for p in config.llm_routing.providers if p.is_local), config.llm_routing.default.provider)
    map_provider = config.llm_routing.provider_by_name(args.map_provider or default_local)
    map_model = args.map_model if map_provider.is_local else resolved.model

    strategies = ["prefix", "rank"]
    swap_start = swap_used_mb()
    if not map_provider.is_local:
        strategies.append("map_reduce")  # remote map → always memory-safe
    elif args.include_local and swap_start <= _MAX_SWAP_START_MB:
        strategies.append("map_reduce")
    elif args.include_local:
        print(f"SKIP map_reduce — LOCAL map but swap already {swap_start:.0f}MB (> {_MAX_SWAP_START_MB}); "
              "free RAM and re-run, or A/B the algorithm with --map-provider <api>.", flush=True)
    print(f"map={map_provider.name}/{map_model} (local={map_provider.is_local}) | "
          f"reduce={resolved.provider.name}/{resolved.model} | strategies={strategies}\n", flush=True)

    papers = load_review_papers(settings, limit=args.papers)
    if not papers:
        print("no built paper briefs — open a deep review first."); return 2
    map_llm = build_client_for_provider(map_provider, map_model, enable_thinking=False)

    results: list[dict[str, Any]] = []
    for title, text in papers:
        for strategy in strategies:
            # rank/prefix don't depend on chunk_chars (single call to max_text_chars) — run once;
            # map_reduce runs once PER chunk size (the sweep dimension).
            sizes = chunk_sizes if strategy == "map_reduce" else [chunk_sizes[0]]
            for size in sizes:
                pre = swap_used_mb()
                r = run_strategy(strategy, title, text, config, reduce_llm=reduce_llm, map_llm=map_llm,
                                 chunk_chars=size, sub_concurrency=1, return_digest=args.faithfulness)
                r["chunk_chars"] = size
                r["swap_delta_mb"] = round(swap_used_mb() - pre, 0)
                line = (f"  {title[:36]:36} {strategy:11} chars={size:<6} late_recall={r['late_recall']:.2f} "
                        f"complete={r['completeness']:.2f} {r['secs']:.0f}s swapΔ={r['swap_delta_mb']:+.0f}MB")
                if args.faithfulness:
                    f = judge_faithfulness(r["digest_text"], text, judge_llm=None,
                                           judge_model=args.judge_model, max_tokens=args.judge_max_tokens)
                    r["faithfulness"] = f
                    line += f" | faith={f['score']} ({f['supported']}/{f['supported']+f['unsupported'] if f['supported'] is not None else '?'}, {f['secs']:.0f}s)"
                print(line, flush=True)
                results.append(r)

    summary = summarize_ab(results)
    if args.faithfulness:
        # Per-strategy mean faithfulness score (the G8 quality metric). None = undecidable judge call.
        by_strat: dict[str, list[float]] = {}
        for r in results:
            sc = (r.get("faithfulness") or {}).get("score")
            if sc is not None:
                by_strat.setdefault(r["strategy"], []).append(sc)
        summary["faithfulness_mean"] = {s: round(sum(v) / len(v), 3) for s, v in by_strat.items()}
        summary["faithfulness_n"] = {s: len(v) for s, v in by_strat.items()}
    print("\n" + json.dumps(summary, indent=2))
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
