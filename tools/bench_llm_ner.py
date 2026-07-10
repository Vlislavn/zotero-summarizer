"""LLM disease-NER baseline for GLiNER2 Phase-0 probe 0b — the decision-relevant
'against what': can a hosted LLM (api.kather.ai) beat the 205M GLiNER2 encoder at
zero-shot disease NER, on the SAME real BC5CDR sentences and the SAME grader? This
is the crux of the whole thesis (small encoder replacing an LLM). Reuses the pure
graders + loader from bench_gliner2 (no duplication).

Run (key from .env):
  export CUSTOM_API_KEY="$(grep -E '^CUSTOM_API_KEY=' .env | cut -d= -f2-)"
  uv run python tools/bench_llm_ner.py --model GPT-OSS-120B --limit 100 --concurrency 4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from _gliner2_lib import _mean, _median, _norm, prf, score_ner
from bench_gliner2 import load_bc5cdr_disease  # network loader, stays with the NER probe

PROMPT = (
    "Extract every DISEASE or disorder mention from the sentence below. Include named "
    "diseases, syndromes, and disorders; EXCLUDE chemicals, drugs, and generic symptoms "
    "that are not named diseases. Return ONLY a strict JSON object of the form "
    '{"diseases": ["...", ...]} with the exact surface strings. Sentence:\n'
)


def llm_diseases(base_url: str, key: str, model: str, sentence: str) -> set[str]:
    """Disease surface strings from one LLM call. Raises ValueError on unparseable
    output so the caller records it as a tri-state error (benchmark boundary — the
    error is counted, never silently turned into an empty set)."""
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 2048,
        "messages": [{"role": "user", "content": PROMPT + sentence}],
    }).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 — configured kather endpoint
        content = json.load(resp)["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError(f"no JSON object in LLM output: {content[:160]!r}")
    obj = json.loads(m.group(0))
    return {_norm(d) for d in obj.get("diseases", []) if str(d).strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="GPT-OSS-120B")
    ap.add_argument("--base-url", default="https://api.kather.ai/v1")
    ap.add_argument("--key-env", default="CUSTOM_API_KEY")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="data/gliner2_bench/llm_ner_report.json")
    args = ap.parse_args()

    key = os.environ.get(args.key_env)
    if not key:
        raise SystemExit(f"{args.key_env} not set (export it from .env first)")

    sentences = load_bc5cdr_disease(args.limit)
    print(f"LLM disease-NER: {args.model} @ {args.base_url} · {len(sentences)} BC5CDR sentences")

    def one(item: tuple[str, list[str]]) -> dict:
        sent, gold_list = item
        t0 = time.time()
        try:
            pred = llm_diseases(args.base_url, key, args.model, sent)
        except Exception as exc:  # noqa: BLE001 — per-sentence tri-state boundary (recorded)
            return {"error": f"{type(exc).__name__}: {exc}", "latency": time.time() - t0}
        return {"pred": pred, "gold": {_norm(g) for g in gold_list}, "latency": time.time() - t0}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(one, sentences))

    strict = {"tp": 0, "fp": 0, "fn": 0}
    lenient = {"tp": 0, "fp": 0, "fn": 0}
    lat, errors, n_with = [], 0, 0
    for r in results:
        if "error" in r:
            errors += 1
            continue
        lat.append(r["latency"])
        n_with += 1 if r["gold"] else 0
        for bucket, len_ in ((strict, False), (lenient, True)):
            tp, fp, fn = score_ner(r["pred"], r["gold"], lenient=len_)
            bucket["tp"] += tp; bucket["fp"] += fp; bucket["fn"] += fn

    rep = {"model": args.model, "dataset": "bc5cdr-test", "n_sentences": len(sentences),
           "n_with_disease": n_with, "n_errors": errors,
           "strict": prf(strict["tp"], strict["fp"], strict["fn"]),
           "lenient": prf(lenient["tp"], lenient["fp"], lenient["fn"]),
           "latency_secs": {"mean": round(_mean(lat), 2), "median": round(_median(lat), 2)}}
    from pathlib import Path
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, indent=2))
    print(f"\nLLM {args.model} disease-NER on {rep['n_sentences']} BC5CDR sentences "
          f"({rep['n_with_disease']} with a disease, {rep['n_errors']} errors):")
    print(f"  strict  F1 {rep['strict']['f1']:.3f} (P {rep['strict']['precision']:.3f} / R {rep['strict']['recall']:.3f})")
    print(f"  lenient F1 {rep['lenient']['f1']:.3f} (P {rep['lenient']['precision']:.3f} / R {rep['lenient']['recall']:.3f})")
    print(f"  latency {rep['latency_secs']['mean']:.2f}s mean/call")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
