"""LLM baseline + agreement scoring for GLiNER2 Phase-0 probe 0c (PaperParameters
schema extraction). There is no independent params gold, so the decision-relevant
question is: can the 205M GLiNER2 encoder (extract_json) reproduce the extraction
of the deep-review LLM it would replace? This runs the SAME 6-field schema through
a hosted LLM (api.kather.ai) on the SAME gold abstracts, joins the encoder output
persisted by ``bench_gliner2.py --probes params``, and reports per-field agreement
+ over-emission + abstention (reusing the pure graders in _gliner2_lib).

Run (encoder side first, then this):
  uv run python tools/bench_gliner2.py --probes params --dump-raw 1
  export CUSTOM_API_KEY="$(grep -E '^CUSTOM_API_KEY=' .env | cut -d= -f2-)"
  uv run python tools/bench_llm_params.py --model GLM-5.2-FP8 --max-chars 2000
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from _gliner2_lib import (
    ABSTAIN_FIXTURE, PARAM_FIELDS, _mean, _median, compare_params, has_value, parse_params,
)

PROMPT = (
    "You extract structured technical parameters from a machine-learning paper's text. "
    "Return ONLY a strict JSON object with EXACTLY these keys:\n"
    '{"dataset": "", "baselines": [], "sample_size": "", "metrics": [], '
    '"architecture": "", "external_validation": null}\n'
    "dataset = main dataset name(s) (string); baselines = list of methods/models compared against; "
    "sample_size = number of samples/patients/images/records (string); metrics = list of evaluation "
    "metrics; architecture = core model/method name (string); external_validation = true/false/null "
    "for validation on an external independent dataset. CRITICAL: if a field is not stated in the "
    'text, return its empty value ("", [], or null) — NEVER guess or fabricate. Paper text:\n'
)


def llm_params(base_url: str, key: str, model: str, text: str) -> dict:
    """One LLM extraction, normalized to the 6-field contract. Raises ValueError on
    unparseable output (tri-state benchmark boundary — recorded, never swallowed)."""
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 2048,
        "messages": [{"role": "user", "content": PROMPT + text}],
    }).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 — configured kather endpoint
        content = json.load(resp)["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError(f"no JSON object in LLM output: {content[:160]!r}")
    return parse_params(json.loads(m.group(0)))


def _encoder_abstain(out_dir: Path) -> str:
    """Encoder abstention result from the bench_gliner2 report (enrichment only; the
    required join input is params_encoder.jsonl). n/a if the report isn't present."""
    rep = out_dir / "report.json"
    if not rep.exists():
        return "n/a (run bench_gliner2 --probes params)"
    pa = json.loads(rep.read_text()).get("tracks", {}).get("param_extraction", {})
    emitted = pa.get("abstain_emitted_fields", [])
    return "CLEAN" if not emitted else f"FABRICATED {emitted}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="GLM-5.2-FP8")
    ap.add_argument("--base-url", default="https://api.kather.ai/v1")
    ap.add_argument("--key-env", default="CUSTOM_API_KEY")
    ap.add_argument("--gold", default="data/paper_quality_bench/gold_v1.jsonl")
    ap.add_argument("--max-chars", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dir", default="data/gliner2_bench", help="dir holding params_encoder.jsonl")
    args = ap.parse_args()

    key = os.environ.get(args.key_env)
    if not key:
        raise SystemExit(f"{args.key_env} not set (export it from .env first)")
    enc_path = Path(args.dir) / "params_encoder.jsonl"
    if not enc_path.exists():
        raise SystemExit(f"{enc_path} missing — run: uv run python tools/bench_gliner2.py --probes params")
    enc = {r["item_key"]: r["params"] for r in
           (json.loads(ln) for ln in enc_path.read_text().splitlines() if ln.strip())}

    rows = [json.loads(ln) for ln in Path(args.gold).read_text().splitlines() if ln.strip()]
    papers = [r for r in rows if r.get("kind") == "paper" and r["item_key"] in enc]
    print(f"LLM PaperParameters: {args.model} @ {args.base_url} · {len(papers)} papers "
          f"(joined to encoder output)")

    def one(p: dict) -> dict:
        text = Path(p["text_path"]).read_text()[:args.max_chars]
        t0 = time.time()
        try:
            pred = llm_params(args.base_url, key, args.model, text)
        except Exception as exc:  # noqa: BLE001 — per-item tri-state boundary (recorded)
            return {"item_key": p["item_key"], "error": f"{type(exc).__name__}: {exc}", "latency": time.time() - t0}
        return {"item_key": p["item_key"], "llm": pred, "latency": time.time() - t0}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(one, papers))

    # per-field verdict tallies across the joined papers
    field_verdicts = {f: Counter() for f in PARAM_FIELDS}
    lat, errors, scored = [], 0, 0
    for r in results:
        if "error" in r:
            errors += 1
            print(f"  [params] {r['item_key']}: {r['error']}")
            continue
        lat.append(r["latency"])
        scored += 1
        for f, verdict in compare_params(enc[r["item_key"]], r["llm"]).items():
            field_verdicts[f][verdict] += 1

    total = {"both_match": 0, "both_mismatch": 0, "enc_only": 0, "llm_only": 0, "both_absent": 0}
    for c in field_verdicts.values():
        for k in total:
            total[k] += c[k]
    cells = sum(total.values()) or 1
    both_present = total["both_match"] + total["both_mismatch"]
    presence_agree = (total["both_match"] + total["both_mismatch"] + total["both_absent"]) / cells
    value_agree = total["both_match"] / both_present if both_present else 0.0

    # LLM abstention on a parameter-less input (must emit nothing)
    llm_abstain = llm_params(args.base_url, key, args.model, ABSTAIN_FIXTURE)
    llm_abstain_emitted = [f for f in PARAM_FIELDS if has_value(f, llm_abstain[f])]

    rep = {"model": args.model, "n_papers": len(papers), "n_scored": scored, "n_errors": errors,
           "field_verdicts": {f: dict(c) for f, c in field_verdicts.items()}, "totals": total,
           "presence_agreement": round(presence_agree, 3), "value_agreement_when_both_present": round(value_agree, 3),
           "encoder_over_emission": total["enc_only"], "encoder_miss": total["llm_only"],
           "encoder_abstain": _encoder_abstain(Path(args.dir)),
           "llm_abstain_emitted_fields": llm_abstain_emitted,
           "latency_secs": {"mean": round(_mean(lat), 2), "median": round(_median(lat), 2)}}
    out = Path(args.dir) / "params_agreement.json"
    out.write_text(json.dumps(rep, indent=2))
    print(f"\nPaperParameters agreement — GLiNER2 encoder vs {args.model} on {scored} papers "
          f"({errors} LLM errors):")
    print(f"  presence agreement (field-level): {rep['presence_agreement']:.3f}")
    print(f"  value agreement when both emit:   {rep['value_agreement_when_both_present']:.3f} "
          f"({both_present} both-present cells)")
    print(f"  encoder over-emission (emits where LLM abstains): {rep['encoder_over_emission']} cells")
    print(f"  encoder miss (LLM emits, encoder blank):          {rep['encoder_miss']} cells")
    print(f"  abstention on param-less input — encoder: {rep['encoder_abstain']} · "
          f"LLM: {'CLEAN' if not llm_abstain_emitted else f'FABRICATED {llm_abstain_emitted}'}")
    print(f"  per-field verdicts: {rep['field_verdicts']}")
    print(f"  LLM latency {rep['latency_secs']['mean']:.2f}s mean/call")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
