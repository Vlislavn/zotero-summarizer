"""Phase-0 sanity benchmark for GLiNER2 (fastino, Apache-2.0, DeBERTa-v2 encoder,
CPU, zero-shot) — the go/no-go BEFORE any product wiring. GLiNER2's README ships
NO accuracy or latency numbers, so this measures whether it is even good enough
to bother integrating, on the two capabilities the repo does NOT already cover
with an encoder (paper-type classification is done by an LLM; clinical-entity NER
by nothing). See the approved plan + services/model/claim_checker.py (the same
optional-dep, encoder-replaces-LLM precedent).

This is a LEAF tool on purpose: stdlib + a gated ``gliner2`` import ONLY — no
zotero_summarizer product import — so GLiNER2 can be judged in a clean env,
decoupled from the pipeline while unproven.

Two probes:
  0a  paper-type classification — classify_text over the 14 leaf PaperType labels
      on the 12 gold abstracts (data/paper_quality_bench/gold_v1.jsonl); report
      top-1 accuracy vs gold_type + confusion. Baseline to beat = the LLM type
      accuracy already benched by tools/bench_paper_quality.py --tracks type.
  0b  disease NER — extract_entities("disease") on an inline biomedical fixture;
      report strict (exact) + lenient (substring-overlap) entity F1 + CPU latency.
      Baseline = published GLiNER/scispaCy disease-NER F1.
  0c  PaperParameters schema extraction — extract_json over the 6 PaperParameters
      fields on the gold abstracts (--probes params). No independent params gold
      exists, so the decision metric is AGREEMENT with the deep-review LLM it would
      replace (computed by the sibling bench_llm_params.py) + an abstention check on
      a parameter-less input (fabricating any field fails the faithbench contract).

Decision: proceed to Phase 1 ONLY for a probe that matches/beats its baseline.

Run:
  uv pip install 'gliner2[local]>=1.3'                       # or: uv sync --extra entities
  # with the MLX 35B UNLOADED (memory-safe; base model is ~0.4-0.8 GB, CPU):
  uv run python tools/bench_gliner2.py --dump-raw 2                     # inline fixture (fast)
  uv run python tools/bench_gliner2.py --ner-dataset bc5cdr --ner-limit 300   # HONEST NER
  uv run python tools/bench_gliner2.py --selfcheck           # logic only, no download

0b defaults to an inline smoke fixture (a wiring floor, NOT accuracy — distrust its
perfect F1); `--ner-dataset bc5cdr` streams the real BC5CDR test set from the HF
datasets-server (parquet-backed JSON; no loader script / dep downgrade). The LLM
'against what' for 0b lives in the sibling tools/bench_llm_ner.py. Family accuracy is
skipped here (secondary); it graduates into bench_paper_quality.py (spec_for).
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

from _gliner2_lib import (  # pure, model-free graders shared with the LLM baselines
    ABSTAIN_FIXTURE, PARAM_FIELDS, PARAM_STRUCT, _mean, _median, _norm,
    compare_params, has_value, parse_params, prf, score_ner, value_match,
)

# The 14 leaf PaperType labels (the two generic_* fallbacks + non_paper are never
# LLM-picked, so they are not classification targets). Kept in sync with
# zotero_summarizer/services/library/_paper_type_checklists.py::PaperType.
LEAF_TYPES = [
    "empirical_ml", "methods_system", "clinical_prediction", "diagnostic_accuracy",
    "rct_ai", "dataset_benchmark", "systematic_review", "narrative_review", "survey",
    "position", "policy", "theory", "case_report", "editorial",
]

# Inline disease-NER smoke fixture (authored, BC5CDR-style; gold = disease surface
# strings per sentence). ponytail: swap for bigbio/bc5cdr if 0b clears the bar.
DISEASE_FIXTURE: list[tuple[str, list[str]]] = [
    ("Patients treated with cisplatin frequently develop nephrotoxicity and peripheral neuropathy.",
     ["nephrotoxicity", "peripheral neuropathy"]),
    ("The trial enrolled adults with type 2 diabetes and diabetic retinopathy.",
     ["type 2 diabetes", "diabetic retinopathy"]),
    ("Long-term use of the drug was associated with hepatotoxicity and acute liver failure.",
     ["hepatotoxicity", "acute liver failure"]),
    ("Screening detected breast cancer in high-risk women.",
     ["breast cancer"]),
    ("The patient presented with pneumonia and subsequent sepsis.",
     ["pneumonia", "sepsis"]),
    ("Mutations in this gene are linked to Parkinson's disease and Alzheimer's disease.",
     ["Parkinson's disease", "Alzheimer's disease"]),
    ("Adverse events included hypertension, headache, and nausea.",
     ["hypertension", "headache", "nausea"]),
    ("Chronic obstructive pulmonary disease progression was slowed by the intervention.",
     ["chronic obstructive pulmonary disease"]),
    ("The cohort study examined stroke risk in patients with atrial fibrillation.",
     ["stroke", "atrial fibrillation"]),
    ("Overexpression of the protein promoted growth in hepatocellular carcinoma.",
     ["hepatocellular carcinoma"]),
    ("Treatment reduced the incidence of myocardial infarction and heart failure.",
     ["myocardial infarction", "heart failure"]),
    ("Children exposed to the toxin showed signs of asthma and eczema.",
     ["asthma", "eczema"]),
    ("The vaccine lowered rates of influenza and pneumonia in the elderly.",
     ["influenza", "pneumonia"]),
    ("Renal impairment and hyperkalemia were reported as serious side effects.",
     ["renal impairment", "hyperkalemia"]),
    ("The study focused on patients with melanoma treated with immunotherapy.",
     ["melanoma"]),
]


# ---- tolerant parsers for GLiNER2's (undocumented) return shapes -----------------

def _first_key(d: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return default


def top_label(raw: Any, key: str = "paper_type") -> str:
    """Best single label from classify_text output. Handles str, list[str],
    list[{label,score}], {label:score}, and the {key: <one-of-those>} wrapper."""
    if isinstance(raw, dict) and key in raw:
        raw = raw[key]
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return max(raw, key=lambda k: raw[k]) if raw else ""
    if isinstance(raw, list) and raw:
        head = raw[0]
        if isinstance(head, str):
            return head
        if isinstance(head, dict):
            best = max(raw, key=lambda d: float(_first_key(d, ("score", "confidence", "prob"), 0.0)))
            return str(_first_key(best, ("label", "class", "text", "name"), ""))
    return ""


def entity_texts(raw: Any, label: str = "disease") -> set[str]:
    """Normalized entity surface strings for one label from extract_entities output.
    Handles {label: [str|{text}]} and flat list[{text,label}]."""
    out: set[str] = set()
    items: list[Any] = []
    if isinstance(raw, dict):
        # GLiNER2 nests NER under an 'entities' key: {'entities': {'disease': [...]}};
        # fall back to raw itself for a flat {label: [...]} shape.
        container = raw.get("entities", raw)
        if isinstance(container, dict):
            items = container.get(label) or []
        elif isinstance(container, list):
            items = [e for e in container
                     if not isinstance(e, dict) or _first_key(e, ("label", "type", "class")) in (label, None)]
    elif isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and _first_key(e, ("label", "type", "class")) in (label, None):
                items.append(e)
            elif isinstance(e, str):
                items.append(e)
    for it in items:
        text = it if isinstance(it, str) else str(_first_key(it, ("text", "span", "value", "mention"), ""))
        if text.strip():
            out.add(_norm(text))
    return out


# ---- GLiNER2 loader (optional-dep boundary, mirrors claim_checker.py) -------------

def load_gliner2(model_name: str):
    try:
        from gliner2 import GLiNER2
    except Exception as exc:  # noqa: BLE001 — optional-dependency boundary
        raise SystemExit(
            f"gliner2 not installed ({type(exc).__name__}). Enable with: "
            "uv sync --extra entities   (or: uv pip install 'gliner2[local]')"
        ) from exc
    print(f"loading {model_name} (downloads once; CPU) ...")
    return GLiNER2.from_pretrained(model_name)


# ---- probes -----------------------------------------------------------------------

def probe_type(extractor, gold_path: Path, max_chars: int, dump_raw: int) -> dict[str, Any]:
    rows = [json.loads(ln) for ln in gold_path.read_text().splitlines() if ln.strip()]
    papers = [r for r in rows if r.get("kind") == "paper"]
    correct, confusion, lat, errors, unparsed = [], Counter(), [], 0, 0
    for i, p in enumerate(papers):
        text = Path(p["text_path"]).read_text()[:max_chars]
        t0 = time.time()
        try:
            raw = extractor.classify_text(text, {"paper_type": LEAF_TYPES})
        except Exception as exc:  # noqa: BLE001 — per-item boundary (tri-state)
            errors += 1
            print(f"  [type] {p['item_key']}: {type(exc).__name__}: {exc}")
            continue
        lat.append(time.time() - t0)
        if i < dump_raw:
            print(f"  [type raw] {p['item_key']} -> {type(raw).__name__}: {str(raw)[:300]}")
        pred = _norm(top_label(raw))
        if not pred:  # parser didn't recognize the shape — loud, not a silent 'wrong'
            unparsed += 1
        gold = _norm(p["gold_type"])
        correct.append(1.0 if pred == gold else 0.0)
        confusion[f"{gold}→{pred or '<unparsed>'}"] += 1
    return {"n_papers": len(papers), "n_scored": len(correct), "n_errors": errors,
            "n_unparsed": unparsed, "top1_accuracy": round(_mean(correct), 4),
            "latency_secs": {"mean": round(_mean(lat), 3), "median": round(_median(lat), 3)},
            "confusion": dict(confusion.most_common())}


def bio_disease_spans(tokens: list[str], tags: list[int], begin: int = 2, inside: int = 3) -> list[str]:
    """Contiguous disease spans from BIO tags (tner/bc5cdr: 2=B-Disease, 3=I-Disease)."""
    spans: list[str] = []
    cur: list[str] = []
    for tok, tag in zip(tokens, tags):
        if tag == begin:
            if cur:
                spans.append(" ".join(cur))
            cur = [tok]
        elif tag == inside and cur:
            cur.append(tok)
        else:
            if cur:
                spans.append(" ".join(cur))
            cur = []
    if cur:
        spans.append(" ".join(cur))
    return spans


def load_bc5cdr_disease(limit: int) -> list[tuple[str, list[str]]]:
    """Real BC5CDR test sentences + gold disease spans, streamed from the HF
    datasets-server (parquet-backed JSON; no loader script, no dep downgrade)."""
    import urllib.request
    base = ("https://datasets-server.huggingface.co/rows"
            "?dataset=tner/bc5cdr&config=bc5cdr&split=test")
    out: list[tuple[str, list[str]]] = []
    offset = 0
    while len(out) < limit:
        n = min(100, limit - len(out))
        with urllib.request.urlopen(f"{base}&offset={offset}&length={n}", timeout=60) as resp:
            rows = json.load(resp).get("rows", [])
        if not rows:
            break
        for item in rows:
            r = item["row"]
            out.append((" ".join(r["tokens"]), bio_disease_spans(r["tokens"], r["tags"])))
        offset += len(rows)
    return out


def probe_ner(extractor, dump_raw: int, sentences: list[tuple[str, list[str]]], dataset: str) -> dict[str, Any]:
    strict = {"tp": 0, "fp": 0, "fn": 0}
    lenient = {"tp": 0, "fp": 0, "fn": 0}
    lat, errors, n_with = [], 0, 0
    for i, (sent, gold_list) in enumerate(sentences):
        gold = {_norm(g) for g in gold_list}
        n_with += 1 if gold else 0
        t0 = time.time()
        try:
            raw = extractor.extract_entities(sent, ["disease"])
        except Exception as exc:  # noqa: BLE001 — per-sentence boundary (recorded, not swallowed)
            errors += 1
            print(f"  [ner] sent {i}: {type(exc).__name__}: {exc}")
            continue
        lat.append(time.time() - t0)
        if i < dump_raw:
            print(f"  [ner raw] {type(raw).__name__}: {str(raw)[:300]}")
        pred = entity_texts(raw, "disease")
        for bucket, len_ in ((strict, False), (lenient, True)):
            tp, fp, fn = score_ner(pred, gold, lenient=len_)
            bucket["tp"] += tp; bucket["fp"] += fp; bucket["fn"] += fn
    return {"dataset": dataset, "n_sentences": len(sentences), "n_with_disease": n_with, "n_errors": errors,
            "strict": prf(strict["tp"], strict["fp"], strict["fn"]),
            "lenient": prf(lenient["tp"], lenient["fp"], lenient["fn"]),
            "latency_secs": {"mean": round(_mean(lat), 3), "median": round(_median(lat), 3)}}


def probe_params(extractor, gold_path: Path, max_chars: int, dump_raw: int, out_dir: Path) -> dict[str, Any]:
    """0c: schema extraction (extract_json) of the 6 PaperParameters fields on the
    gold abstracts. There is no independent params gold, so this persists the
    encoder's per-paper output for the LLM-agreement join (bench_llm_params.py) and
    reports the abstention check on a parameter-less input here (self-contained)."""
    rows = [json.loads(ln) for ln in gold_path.read_text().splitlines() if ln.strip()]
    papers = [r for r in rows if r.get("kind") == "paper"]
    per_paper, lat, errors = [], [], 0
    for i, p in enumerate(papers):
        text = Path(p["text_path"]).read_text()[:max_chars]
        t0 = time.time()
        try:
            raw = extractor.extract_json(text, PARAM_STRUCT)
        except Exception as exc:  # noqa: BLE001 — per-item boundary (recorded, not swallowed)
            errors += 1
            print(f"  [params] {p['item_key']}: {type(exc).__name__}: {exc}")
            continue
        lat.append(time.time() - t0)
        if i < dump_raw:
            print(f"  [params raw] {p['item_key']} -> {str(raw)[:300]}")
        per_paper.append({"item_key": p["item_key"], "gold_type": p.get("gold_type", ""),
                          "params": parse_params(raw)})
    # abstention: a parameter-less input must yield all-empty fields (no fabrication).
    abstain = parse_params(extractor.extract_json(ABSTAIN_FIXTURE, PARAM_STRUCT))
    abstain_emitted = [f for f in PARAM_FIELDS if has_value(f, abstain[f])]
    enc_path = out_dir / "params_encoder.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)
    enc_path.write_text("\n".join(json.dumps(r) for r in per_paper))
    field_emit = {f: sum(1 for r in per_paper if has_value(f, r["params"][f])) for f in PARAM_FIELDS}
    return {"n_papers": len(papers), "n_scored": len(per_paper), "n_errors": errors,
            "field_emit_counts": field_emit, "abstain_emitted_fields": abstain_emitted,
            "latency_secs": {"mean": round(_mean(lat), 3), "median": round(_median(lat), 3)},
            "encoder_dump": str(enc_path)}


# ---- report -----------------------------------------------------------------------

def report_md(rep: dict[str, Any]) -> str:
    lines = [f"# GLiNER2 Phase-0 sanity — {rep['model']}",
             "_go/no-go before any product wiring; leaf tool, no product import_\n"]
    t = rep["tracks"].get("type_classification")
    if t:
        lines += ["## 0a. Paper-type classification (14-label zero-shot)",
                  f"- top-1 accuracy: **{t['top1_accuracy']:.3f}** "
                  f"({t['n_scored']}/{t['n_papers']} scored, {t['n_errors']} errors, "
                  f"{t['n_unparsed']} unparsed → if high, fix the parser via --dump-raw)",
                  f"- latency: {t['latency_secs']['mean']:.2f}s mean / {t['latency_secs']['median']:.2f}s median",
                  f"- baseline to beat: the LLM type accuracy from bench_paper_quality.py --tracks type",
                  f"- confusion (gold→pred): `{t['confusion']}`\n"]
    n = rep["tracks"].get("disease_ner")
    if n:
        trivial = " (⚠ self-authored — wiring floor, not accuracy)" if n["dataset"] == "inline-fixture" else ""
        lines += [f"## 0b. Disease NER (zero-shot, dataset: {n['dataset']}{trivial})",
                  f"- **lenient F1 {n['lenient']['f1']:.3f}** (P {n['lenient']['precision']:.3f} / "
                  f"R {n['lenient']['recall']:.3f}) — headline for the go/no-go",
                  f"- strict F1 {n['strict']['f1']:.3f} (P {n['strict']['precision']:.3f} / "
                  f"R {n['strict']['recall']:.3f}) — exact-match floor",
                  f"- latency: {n['latency_secs']['mean']:.3f}s mean/call · {n['n_sentences']} sentences "
                  f"({n['n_with_disease']} with a disease), {n['n_errors']} errors",
                  f"- baseline: published GLiNER/scispaCy disease-NER F1 (fine-tuned ~0.85; zero-shot lower)\n"]
    pa = rep["tracks"].get("param_extraction")
    if pa:
        abst = pa["abstain_emitted_fields"]
        lines += ["## 0c. PaperParameters schema extraction (extract_json, zero-shot)",
                  f"- scored {pa['n_scored']}/{pa['n_papers']} papers, {pa['n_errors']} errors; "
                  f"latency {pa['latency_secs']['mean']:.3f}s mean/call",
                  f"- per-field emit counts: `{pa['field_emit_counts']}`",
                  f"- abstention on a parameter-less input: "
                  f"{'CLEAN (all empty)' if not abst else f'FABRICATED {abst}'} "
                  "— faithbench contract: must be empty",
                  f"- agreement vs the deep-review LLM it would replace: see bench_llm_params.py "
                  f"(joins `{Path(pa['encoder_dump']).name}`)\n"]
    return "\n".join(lines)


# ---- self-check (no model; validates the pure logic) ------------------------------

def selfcheck() -> int:
    assert _norm("  Lung   Cancer ") == "lung cancer"
    assert prf(2, 1, 1)["f1"] == round(2 / 3, 4)
    assert score_ner({"lung cancer", "copd"}, {"lung cancer", "asthma"}, lenient=False) == (1, 1, 1)
    # lenient absorbs modifier drift: pred 'metastatic melanoma' ⊇ gold 'melanoma'
    assert score_ner({"metastatic melanoma"}, {"melanoma"}, lenient=True) == (1, 0, 0)
    assert score_ner({"metastatic melanoma"}, {"melanoma"}, lenient=False) == (0, 1, 1)
    assert top_label("empirical_ml") == "empirical_ml"
    assert top_label({"paper_type": "rct_ai"}) == "rct_ai"
    assert top_label(["survey", "position"]) == "survey"
    assert top_label([{"label": "x", "score": 0.2}, {"label": "y", "score": 0.9}]) == "y"
    assert top_label({"paper_type": {"theory": 0.1, "policy": 0.8}}) == "policy"
    assert entity_texts({"disease": ["Lung Cancer", {"text": "COPD"}]}) == {"lung cancer", "copd"}
    # GLiNER2's real shape nests under 'entities' (verified via --dump-raw)
    assert entity_texts({"entities": {"disease": ["Nephrotoxicity", {"text": "COPD"}]}}) == {"nephrotoxicity", "copd"}
    assert entity_texts([{"text": "Sepsis", "label": "disease"}]) == {"sepsis"}
    assert bio_disease_spans(["Famotidine", "-", "associated", "delirium", "."], [1, 0, 0, 2, 0]) == ["delirium"]
    assert bio_disease_spans(["chronic", "renal", "failure"], [2, 3, 3]) == ["chronic renal failure"]
    assert math.isclose(prf(0, 0, 0)["f1"], 0.0)
    # 0c param graders: parse the real extract_json shape, presence, agreement
    _p = parse_params({"paper": [{"dataset": "ChestX-ray14", "baselines": [], "sample_size": "100000",
                                  "metrics": ["F1"], "architecture": "DenseNet", "external_validation": "yes"}]})
    assert _p["dataset"] == "chestx-ray14" and _p["metrics"] == ["f1"]
    assert _p["baselines"] == [] and _p["external_validation"] is True
    assert parse_params({"paper": [{}]})["external_validation"] is None  # null → abstain
    assert has_value("metrics", ["f1"]) and not has_value("metrics", [])
    assert has_value("external_validation", False) and not has_value("external_validation", None)
    assert value_match("dataset", "chestx-ray14", "the chestx-ray14 set")  # substring overlap
    assert value_match("metrics", ["auc", "f1"], ["f1 score"])  # lenient list overlap
    assert not value_match("external_validation", True, False)
    _cmp = compare_params(
        {"dataset": "imagenet", "baselines": [], "sample_size": "", "metrics": ["auc"], "architecture": "", "external_validation": True},
        {"dataset": "coco", "baselines": ["b"], "sample_size": "", "metrics": ["auc"], "architecture": "", "external_validation": None})
    assert _cmp["dataset"] == "both_mismatch" and _cmp["metrics"] == "both_match"
    assert _cmp["baselines"] == "llm_only" and _cmp["external_validation"] == "enc_only"
    assert _cmp["sample_size"] == "both_absent"
    print("selfcheck OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="fastino/gliner2-base-v1")
    ap.add_argument("--gold", default="data/paper_quality_bench/gold_v1.jsonl")
    ap.add_argument("--max-chars", type=int, default=2000,
                    help="chars of paper text fed to classify_text (encoder token limit ~512)")
    ap.add_argument("--probes", default="type,ner", help="comma of: type,ner,params")
    ap.add_argument("--ner-dataset", choices=["fixture", "bc5cdr"], default="fixture",
                    help="fixture = inline smoke set (trivial); bc5cdr = real HF test set (honest)")
    ap.add_argument("--ner-limit", type=int, default=300, help="max BC5CDR sentences to score")
    ap.add_argument("--dump-raw", type=int, default=0, help="print first N raw API results per probe")
    ap.add_argument("--out", default="data/gliner2_bench")
    ap.add_argument("--selfcheck", action="store_true", help="run pure-logic asserts only; no model")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    extractor = load_gliner2(args.model)
    probes = set(args.probes.split(","))
    rep: dict[str, Any] = {"model": args.model, "tracks": {}}
    if "type" in probes:
        print("probe 0a: paper-type classification ...")
        rep["tracks"]["type_classification"] = probe_type(extractor, Path(args.gold), args.max_chars, args.dump_raw)
    if "ner" in probes:
        if args.ner_dataset == "bc5cdr":
            print(f"probe 0b: disease NER on real BC5CDR test (limit {args.ner_limit}) ...")
            sentences, ds_name = load_bc5cdr_disease(args.ner_limit), "bc5cdr-test"
        else:
            print("probe 0b: disease NER on inline smoke fixture ...")
            sentences, ds_name = DISEASE_FIXTURE, "inline-fixture"
        rep["tracks"]["disease_ner"] = probe_ner(extractor, args.dump_raw, sentences, ds_name)
    if "params" in probes:
        print("probe 0c: PaperParameters schema extraction (extract_json) ...")
        rep["tracks"]["param_extraction"] = probe_params(
            extractor, Path(args.gold), args.max_chars, args.dump_raw, Path(args.out))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(rep, indent=2))
    md = report_md(rep)
    (out_dir / "report.md").write_text(md)
    print("\n" + md)
    print(f"wrote {out_dir}/report.json + report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
