"""Benchmark the type-aware quality-eval pipeline against an Opus-authored, frozen,
FIREWALLED gold set — paper-type detection, checklist↔gold agreement, the self-verify
2nd pass, and Docling-vs-fitz extraction. Run by hand (LLM-driven; tools/, not a unit
test). The pure graders + stats are importable for tests/test_bench_paper_quality.py.

Discipline mirrors faithbench `_stats`: mean AND median, std/SEM across run-means
(ddof=1), tri-state (a parse/LLM error leaves the denominator), per-type breakdown.
Grading is DETERMINISTIC exact-match vs the gold — Opus is the gold AUTHOR, never a
runtime judge, and the run stage reads only paper TEXT (+ the controlled gold TYPE for
the checklist track, to isolate rubric quality from detection error), never the graded
labels (rubric/band/over-claim). See data/paper_quality_bench/gold_v1.jsonl.

Run:
  OPENAI_API_KEY=ollama uv run python tools/bench_paper_quality.py \
      --gold data/paper_quality_bench/gold_v1.jsonl --runs 3 --tracks type,checklist,selfverify,docling
Stages auto-resume by (track,key,run); pass --stage run|grade|report|all (default all).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ---- tiny stats (faithbench `_stats` discipline, inlined to keep this a leaf tool) ----

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _std_sem_across_runs(per_run: dict[int, list[float]]) -> dict[str, float]:
    """Sample std (ddof=1) + SEM over per-run means; 0.0 when runs ≤ 1 (pooling
    correlated repeats would fake-tighten the bars — faithbench rule)."""
    means = [_mean(v) for _, v in sorted(per_run.items()) if v]
    if len(means) <= 1:
        return {"mean": round(_mean(means), 4), "std": 0.0, "sem": 0.0, "n_runs": len(means)}
    mu = _mean(means)
    std = math.sqrt(sum((m - mu) ** 2 for m in means) / (len(means) - 1))
    return {"mean": round(mu, 4), "median": round(_median(means), 4),
            "std": round(std, 4), "sem": round(std / math.sqrt(len(means)), 4), "n_runs": len(means)}


# ---- pure deterministic graders (unit-tested; no LLM, no I/O) -------------------------

def grade_type(pred_type: str, gold_type: str, pred_family: str, gold_family: str) -> dict[str, Any]:
    """Top-1 type match + coarser family match (EMP/SYNTH/ARG/CLIN). A `generic_*`
    fallback counts as the right FAMILY but a missed leaf type."""
    return {"type_correct": pred_type == gold_type, "family_correct": pred_family == gold_family,
            "fallback": pred_type.startswith("generic_")}


def grade_checklist(pred_rubric: dict[str, str], gold_rubric: dict[str, str]) -> dict[str, Any]:
    """Aligned (pred,gold) verdict pairs over the gold's item keys (for pooled Cohen's
    κ) + the agreement rate. Missing pred key → 'na' (the pipeline didn't answer it)."""
    keys = list(gold_rubric)
    pred = [str(pred_rubric.get(k, "na")).lower() for k in keys]
    gold = [str(gold_rubric[k]).lower() for k in keys]
    agree = sum(1 for p, g in zip(pred, gold) if p == g)
    return {"pred": pred, "gold": gold, "n_items": len(keys),
            "agreement": round(agree / len(keys), 4) if keys else 0.0}


_BAND_ORDER = {"flag": 0, "neutral": 1, "highlight": 2}


def grade_band(pred_band: str, gold_band: str) -> dict[str, Any]:
    """Exact + within-±1 on the ordered flag<neutral<highlight. `uncertain` (run
    disagreement) only matches exactly — it isn't on the ordinal axis."""
    exact = pred_band == gold_band
    if pred_band in _BAND_ORDER and gold_band in _BAND_ORDER:
        within1 = abs(_BAND_ORDER[pred_band] - _BAND_ORDER[gold_band]) <= 1
    else:
        within1 = exact
    return {"exact": exact, "within1": within1}


def grade_selfverify(pred_overclaim: bool, is_overclaim: bool) -> str:
    """Confusion bucket. Over-claim = the POSITIVE class (we want to catch it)."""
    if is_overclaim and pred_overclaim:
        return "TP"
    if is_overclaim and not pred_overclaim:
        return "FN"   # missed an over-claim
    if not is_overclaim and pred_overclaim:
        return "FP"   # demoted a LEGIT grounding — the unacceptable error
    return "TN"


def grade_docling(extracted: int, truth: int) -> float | None:
    """Recall vs the Opus truth count; None when truth==0 (undefined, leaves denom)."""
    if truth <= 0:
        return None
    return round(min(extracted, truth) / truth, 4)


def selfverify_metrics(buckets: Counter) -> dict[str, float]:
    tp, fp, fn, tn = buckets["TP"], buckets["FP"], buckets["FN"], buckets["TN"]
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


# ---- cohen kappa (reuse the in-repo implementation) -----------------------------------

def _kappa(pred: list[str], gold: list[str]) -> float | None:
    from zotero_summarizer.services.library.quality_calibration import cohen_kappa
    return cohen_kappa(pred, gold)


# ---- gold I/O + firewall --------------------------------------------------------------

_LABEL_FIELDS = ("gold_rubric", "gold_band", "gold_grade", "is_overclaim")  # never read in `run`


def load_gold(path: Path) -> dict[str, Any]:
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    meta = next((r for r in rows if r.get("kind") == "meta"), {})
    papers = [r for r in rows if r.get("kind") == "paper"]
    pairs = [r for r in rows if r.get("kind") == "selfverify"]
    return {"meta": meta, "papers": papers, "pairs": pairs}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidate_view(paper: dict[str, Any]) -> dict[str, Any]:
    """The ONLY paper fields the pipeline (`run`) may see — enforces the firewall:
    text + the controlled gold TYPE (to isolate the checklist track), never the graded
    rubric/band/grade labels."""
    return {"item_key": paper["item_key"], "text_path": paper["text_path"],
            "pdf_path": paper.get("pdf_path", ""), "gold_type": paper["gold_type"]}


# ---- run stage (LLM-driven) -----------------------------------------------------------

def _extract_json(txt: str) -> dict[str, Any] | None:
    """First balanced {...} object in the model's text, or None (drives the retry)."""
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class _OllamaChat:
    """LLM client hitting ollama's NATIVE /api/chat with ``think:false`` — the /v1
    OpenAI-compat path IGNORES thinking-off, so qwen3 over-reasons (minutes/call); the
    native ``think:false`` disables it (sub-second). Implements the
    ``.pydantic_prompt(prompt, pydantic_model)`` protocol the pipeline calls. One
    reinforced retry (mirrors the pipeline's own retry-after); a parse failure RAISES
    so the benchmark's per-trial boundary records it (tri-state — leaves the denominator)."""

    def __init__(self, base_url: str, model: str):
        url = base_url.rstrip("/")
        self.url = (url[:-3] if url.endswith("/v1") else url) + "/api/chat"
        self.model = model

    def _call(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model, "think": False, "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"num_predict": 4096, "temperature": 0},
        }).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 — localhost ollama
            return json.loads(resp.read()).get("message", {}).get("content") or ""

    def pydantic_prompt(self, *, prompt: str, pydantic_model: Any) -> Any:
        last = ""
        for attempt in range(2):
            text = self._call(prompt if attempt == 0 else
                              prompt + "\n\nReturn ONLY the strict JSON object, no prose.")
            obj = _extract_json(text)
            if obj is not None:
                try:
                    return pydantic_model(**obj)
                except (TypeError, ValueError):
                    last = text  # schema mismatch — reinforce + retry once
                    continue
            last = text
        raise ValueError(f"{pydantic_model.__name__}: unparseable model output: {last[:200]!r}")


def _client(provider_name: str, base_url: str, model: str):
    """Local default = the native-/api/chat think:false shim (see _OllamaChat).
    ``--provider sota`` instead routes to the SAME client production deep-review uses
    (kather/sota, from goals.yaml ``llm_routing.deep_review``) — measures the real grader."""
    if provider_name == "sota":
        from zotero_summarizer.models.providers import resolve_stage
        from zotero_summarizer.services._common import read_config
        from zotero_summarizer.services.llm.factory import build_client_for_stage
        from zotero_summarizer.settings import Settings
        routing = read_config(Settings.load().config_path).llm_routing
        return build_client_for_stage(resolve_stage(routing, "deep_review"))
    return _OllamaChat(base_url, model)


def _run_paper(llm, view: dict[str, Any], runs: int, self_consistency: int, max_chars: int,
               tracks: set[str]) -> list[dict[str, Any]]:
    from zotero_summarizer.services.library import paper_type as pt, quality_eval as qe
    from zotero_summarizer.services.library._paper_type_checklists import Family, spec_for, PaperType
    text = Path(view["text_path"]).read_text()
    abstract = text[:1500]
    out = []
    for r in range(runs):
        if "type" in tracks:
            rec: dict[str, Any] = {"track": "type", "key": view["item_key"], "run": r}
            t0 = time.time()
            try:
                det = pt.detect(title="", abstract=abstract, headings=[], full_text=text,
                                item_type=None, llm=llm)
                rec.update(status="ok", pred_type=det["type"],
                           pred_family=",".join(sorted(f.value for f in spec_for(det["type"]).families)),
                           latency=round(time.time() - t0, 2))
            except Exception as exc:  # noqa: BLE001 — trial boundary; recorded as harness fault
                rec.update(status="error", error=f"{type(exc).__name__}: {exc}", latency=round(time.time() - t0, 2))
            out.append(rec)

        if "checklist" not in tracks:
            continue
        # checklist track: gold TYPE is a controlled input (isolates rubric from detection)
        crec: dict[str, Any] = {"track": "checklist", "key": view["item_key"], "run": r}
        t0 = time.time()
        try:
            q = qe.evaluate_quality(title="", full_text=text, sections=[],
                                    digest={"tldr": abstract, "key_findings": []}, llm=llm,
                                    max_chars=max_chars, paper_type=view["gold_type"],
                                    self_consistency_runs=self_consistency, self_verification=True)
            crec.update(status="ok", pred_rubric=q.rubric, pred_band=q.quality_band,
                        pred_grade=q.grade, coverage_fraction=q.coverage_fraction,
                        latency=round(time.time() - t0, 2))
        except Exception as exc:  # noqa: BLE001
            crec.update(status="error", error=f"{type(exc).__name__}: {exc}", latency=round(time.time() - t0, 2))
        out.append(crec)
    return out


def _run_pair(llm, pair: dict[str, Any], runs: int) -> list[dict[str, Any]]:
    from zotero_summarizer.services.library import quality_eval as qe
    from zotero_summarizer.services.library._paper_type_checklists import ChecklistItem, ChecklistSpec, Family
    key = pair["criterion_key"]
    spec = ChecklistSpec(families=(Family.CLIN,), standards=(("bench", "http://bench"),),
                         items=(ChecklistItem(key, pair["criterion_text"], "bench", "http://bench", critical=True),))
    out = []
    for r in range(runs):
        rec = {"track": "selfverify", "key": pair["id"], "run": r}
        t0 = time.time()
        try:
            demoted = qe._self_verify(llm, spec=spec, rubric={key: "yes"},
                                      evidence={key: pair["quote"]}, grounded_yes={key})
            rec.update(status="ok", pred_overclaim=key in demoted, latency=round(time.time() - t0, 2))
        except Exception as exc:  # noqa: BLE001
            rec.update(status="error", error=f"{type(exc).__name__}: {exc}", latency=round(time.time() - t0, 2))
        out.append(rec)
    return out


def _run_docling(paper: dict[str, Any]) -> dict[str, Any]:
    from zotero_summarizer.services.library import _paper_read_pdf
    pdf = Path(paper["pdf_path"])
    rec: dict[str, Any] = {"track": "docling", "key": paper["item_key"], "run": 0}
    if not pdf.exists():
        return {**rec, "status": "error", "error": "pdf missing"}
    t0 = time.time()
    fitz = _paper_read_pdf.extract_pdf_content(pdf, use_docling=False)
    t1 = time.time()
    doc = _paper_read_pdf.extract_pdf_content(pdf, use_docling=True)
    return {**rec, "status": "ok",
            "fitz_tables": len(fitz.get("tables") or []), "fitz_figs": len(fitz.get("figures") or []),
            "doc_tables": len(doc.get("tables") or []), "doc_figs": len(doc.get("figures") or []),
            "fitz_secs": round(t1 - t0, 2), "doc_secs": round(time.time() - t1, 2)}


def stage_run(gold: dict[str, Any], llm, runs: int, sc: int, tracks: set[str],
              out_path: Path, limit: int | None, max_chars: int) -> None:
    done = {(r["track"], r["key"], r["run"]) for r in _read_jsonl(out_path)}
    papers = gold["papers"][:limit] if limit else gold["papers"]
    with out_path.open("a") as fh:
        for p in papers:
            view = candidate_view(p)
            if {"type", "checklist"} & tracks:
                for rec in _run_paper(llm, view, runs, sc, max_chars, tracks):
                    if (rec["track"], rec["key"], rec["run"]) not in done and rec["track"] in tracks:
                        fh.write(json.dumps(rec) + "\n"); fh.flush()
            if "docling" in tracks:
                rec = _run_docling(p)
                if (rec["track"], rec["key"], rec["run"]) not in done:
                    fh.write(json.dumps(rec) + "\n"); fh.flush()
        if "selfverify" in tracks:
            for pair in gold["pairs"]:
                for rec in _run_pair(llm, pair, runs):
                    if (rec["track"], rec["key"], rec["run"]) not in done:
                        fh.write(json.dumps(rec) + "\n"); fh.flush()


# ---- grade + report -------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()] if path.exists() else []


def build_report(gold: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    from zotero_summarizer.services.library._paper_type_checklists import spec_for
    gp = {p["item_key"]: p for p in gold["papers"]}
    gpair = {p["id"]: p for p in gold["pairs"]}
    rep: dict[str, Any] = {"meta": gold.get("meta", {}), "tracks": {}}

    # Track 1: type detection
    trows = [r for r in responses if r["track"] == "type" and r["status"] == "ok"]
    if trows:
        per_run: dict[int, list[float]] = defaultdict(list)
        per_run_fam: dict[int, list[float]] = defaultdict(list)
        confusion: Counter = Counter()
        fallback = 0
        for r in trows:
            g = gp[r["key"]]
            res = grade_type(r["pred_type"], g["gold_type"], r["pred_family"], g["gold_family"])
            per_run[r["run"]].append(1.0 if res["type_correct"] else 0.0)
            per_run_fam[r["run"]].append(1.0 if res["family_correct"] else 0.0)
            confusion[f'{g["gold_type"]}→{r["pred_type"]}'] += 1
            fallback += 1 if res["fallback"] else 0
        rep["tracks"]["type_detection"] = {
            "n_trials": len(trows), "n_errors": sum(1 for r in responses if r["track"] == "type" and r["status"] != "ok"),
            "top1_accuracy": _std_sem_across_runs(per_run),
            "family_accuracy": _std_sem_across_runs(per_run_fam),
            "fallback_rate": round(fallback / len(trows), 4),
            "confusion": dict(confusion.most_common()),
        }

    # Track 2: checklist coverage
    crows = [r for r in responses if r["track"] == "checklist" and r["status"] == "ok"]
    if crows:
        pooled_pred: list[str] = []
        pooled_gold: list[str] = []
        per_type_pairs: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
        band_exact: list[float] = []
        band_within1: list[float] = []
        cov_abs_err: list[float] = []
        for r in crows:
            g = gp[r["key"]]
            ck = grade_checklist(r["pred_rubric"], g["gold_rubric"])
            pooled_pred += ck["pred"]; pooled_gold += ck["gold"]
            pt_pred, pt_gold = per_type_pairs[g["gold_type"]]
            pt_pred += ck["pred"]; pt_gold += ck["gold"]
            bg = grade_band(r["pred_band"], g["gold_band"])
            band_exact.append(1.0 if bg["exact"] else 0.0)
            band_within1.append(1.0 if bg["within1"] else 0.0)
            if g.get("gold_coverage_fraction") is not None:
                cov_abs_err.append(abs(float(r["coverage_fraction"]) - float(g["gold_coverage_fraction"])))
        rep["tracks"]["checklist_coverage"] = {
            "n_trials": len(crows),
            "item_cohen_kappa": _kappa(pooled_pred, pooled_gold),
            "item_agreement": round(_mean([1.0 if p == g else 0.0 for p, g in zip(pooled_pred, pooled_gold)]), 4),
            "per_type_kappa": {t: _kappa(pp, gg) for t, (pp, gg) in per_type_pairs.items()},
            "band_exact_match": round(_mean(band_exact), 4),
            "band_within1": round(_mean(band_within1), 4),
            "coverage_fraction_mae": round(_mean(cov_abs_err), 4) if cov_abs_err else None,
        }

    # Track 3: self-verify
    srows = [r for r in responses if r["track"] == "selfverify" and r["status"] == "ok"]
    if srows:
        buckets: Counter = Counter()
        by_kind: dict[str, Counter] = defaultdict(Counter)
        for r in srows:
            p = gpair[r["key"]]
            b = grade_selfverify(bool(r["pred_overclaim"]), bool(p["is_overclaim"]))
            buckets[b] += 1
            by_kind[p.get("overclaim_kind", "legit" if not p["is_overclaim"] else "overclaim")][b] += 1
        rep["tracks"]["self_verify"] = {
            "n_trials": len(srows), **selfverify_metrics(buckets),
            "by_kind": {k: selfverify_metrics(v) for k, v in by_kind.items()},
        }

    # Track 4: docling vs fitz
    drows = [r for r in responses if r["track"] == "docling" and r["status"] == "ok"]
    if drows:
        d_tab, f_tab, d_fig, f_fig, d_lat = [], [], [], [], []
        for r in drows:
            g = gp[r["key"]]
            if (tr := grade_docling(r["doc_tables"], g.get("tables_truth", 0))) is not None:
                d_tab.append(tr); f_tab.append(grade_docling(r["fitz_tables"], g["tables_truth"]) or 0.0)
            if (fr := grade_docling(r["doc_figs"], g.get("figures_truth", 0))) is not None:
                d_fig.append(fr); f_fig.append(grade_docling(r["fitz_figs"], g["figures_truth"]) or 0.0)
            d_lat.append(r["doc_secs"])
        rep["tracks"]["docling_extraction"] = {
            "n_pdfs": len(drows),
            "table_recall": {"docling_mean": round(_mean(d_tab), 4), "fitz_mean": round(_mean(f_tab), 4),
                             "docling_median": round(_median(d_tab), 4)},
            "figure_recall": {"docling_mean": round(_mean(d_fig), 4), "fitz_mean": round(_mean(f_fig), 4)},
            "docling_latency_secs": {"mean": round(_mean(d_lat), 2), "median": round(_median(d_lat), 2)},
        }
    return rep


def report_md(rep: dict[str, Any]) -> str:
    m = rep.get("meta", {})
    lines = [f"# Paper-quality benchmark — {m.get('model_default','?')} vs Opus gold {m.get('version','?')}",
             f"_gold judge: {m.get('judge','opus-4.8')} · git {m.get('git_commit','?')[:8]} · "
             f"{len(rep.get('tracks',{}))} tracks · grading: deterministic exact-match (no runtime judge)_\n"]
    t = rep["tracks"]
    if "type_detection" in t:
        d = t["type_detection"]
        lines += ["## 1. Paper-type detection",
                  f"- top-1 accuracy: **{d['top1_accuracy']['mean']:.3f}** ± {d['top1_accuracy']['sem']:.3f} "
                  f"(median {d['top1_accuracy'].get('median', d['top1_accuracy']['mean']):.3f}, {d['top1_accuracy']['n_runs']} runs)",
                  f"- family accuracy: **{d['family_accuracy']['mean']:.3f}** ± {d['family_accuracy']['sem']:.3f}",
                  f"- structural-fallback rate: {d['fallback_rate']:.3f} · errors: {d['n_errors']}",
                  f"- confusion (gold→pred): `{d['confusion']}`\n"]
    if "checklist_coverage" in t:
        d = t["checklist_coverage"]
        lines += ["## 2. Checklist ↔ Opus agreement",
                  f"- per-item Cohen's κ: **{d['item_cohen_kappa']}** · raw agreement {d['item_agreement']:.3f}",
                  f"- band exact-match: {d['band_exact_match']:.3f} · within-±1: {d['band_within1']:.3f} · "
                  f"coverage-fraction MAE: {d['coverage_fraction_mae']}",
                  f"- per-type κ: `{d['per_type_kappa']}`\n"]
    if "self_verify" in t:
        d = t["self_verify"]
        lines += ["## 3. Self-verification (over-claim catcher)",
                  f"- precision **{d['precision']:.3f}** · recall **{d['recall']:.3f}** · F1 {d['f1']:.3f}",
                  f"- **false-positive rate on legit groundings: {d['false_positive_rate']:.3f}** "
                  f"(TP={d['tp']} FP={d['fp']} FN={d['fn']} TN={d['tn']})",
                  f"- by kind: `{ {k: (v['precision'], v['recall']) for k, v in d['by_kind'].items()} }`\n"]
    if "docling_extraction" in t:
        d = t["docling_extraction"]
        lines += ["## 4. Docling vs fitz extraction",
                  f"- table recall: docling **{d['table_recall']['docling_mean']:.3f}** vs fitz "
                  f"{d['table_recall']['fitz_mean']:.3f}  (fitz≈0 = wiring floor)",
                  f"- figure recall: docling **{d['figure_recall']['docling_mean']:.3f}** vs fitz "
                  f"{d['figure_recall']['fitz_mean']:.3f}",
                  f"- docling latency: {d['docling_latency_secs']['mean']:.1f}s mean ({d['n_pdfs']} PDFs)\n"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", default="data/paper_quality_bench/gold_v1.jsonl")
    ap.add_argument("--provider", default="default")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--self-consistency", type=int, default=1)
    ap.add_argument("--max-chars", type=int, default=12000,
                    help="LLM-fed text cap — default 12000 = the lean tier ollama actually runs in production")
    ap.add_argument("--tracks", default="type,checklist,selfverify,docling")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--stage", choices=["run", "report", "all"], default="all")
    args = ap.parse_args()

    gold = load_gold(Path(args.gold))
    gold.setdefault("meta", {})["model_default"] = args.model
    out_dir = Path(args.out or f"data/paper_quality_bench/runs/{args.model.replace(':', '_')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    resp_path = out_dir / "responses.jsonl"
    tracks = set(args.tracks.split(","))

    if args.stage in ("run", "all"):
        llm = _client(args.provider, args.base_url, args.model)
        print(f"running {args.model} @ {args.base_url} · runs={args.runs} sc={args.self_consistency} tracks={tracks}")
        stage_run(gold, llm, args.runs, args.self_consistency, tracks, resp_path, args.limit, args.max_chars)

    if args.stage in ("report", "all"):
        rep = build_report(gold, _read_jsonl(resp_path))
        (out_dir / "report.json").write_text(json.dumps(rep, indent=2))
        md = report_md(rep)
        (out_dir / "report.md").write_text(md)
        print("\n" + md)
        print(f"\nwrote {out_dir}/report.json + report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
