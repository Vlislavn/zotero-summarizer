"""Triage-LLM score-calibration benchmark: does the feed-stage LLM's 1-5
``relevance_score`` + the 5 ``triage_dimensions`` (esp. ``methodological_rigor``)
correlate with the user's kept/trashed labels — or are they a guess?

WHY THIS EXISTS. The deep-review *grader* was benched (``bench_paper_quality``:
F1 .923), but the TRIAGE LLM's own 1-5 score + per-dimension rubric was NEVER
validated on labels. This is the gap behind the 0.268 composite-only AUC finding
(the gate, not the LLM score). The user asked "did we benchmark the triage LLM
for limits? I expect pre-extraction of some parameters" — the score/dimensions
ARE the LLM's pre-extracted structured judgment, and this measures whether they
track user quality.

WHAT IT MEASURES (2026-06-28 DB):
* LLM ``relevance_score`` (1-5) vs labels — AUC, firewalled (user_approved vs
  user_rejected), bootstrap 95% CI. Compared head-to-head with:
    - ``composite_score`` (the ML gate, the 0.268 finding)
    - goal_sim alone (0.755 title-only floor from eval_relevancy_titleonly)
* ``relevance_score`` vs ``composite_score`` — Spearman ρ (does the LLM agree
  with the gate, or operate orthogonally?)
* per-dimension (goal_alignment, novelty_for_goals, methodological_rigor,
  actionability, evidence_strength) vs proxy metrics + vs composite_score —
  Spearman ρ. ``methodological_rigor`` is the abstract-based rigor guess the
  user suspected; if it's near-chance vs proxies, that's the receipt for
  Part B's full-text parameter extraction.

DATA SHAPE (printed, not hidden):
* The LLM ``relevance_score`` IS persisted (``summary.relevance_score``) on 776
  firewalled-labeled rows (15 kept / 761 trashed) — MEASURABLE, but kept is at
  the 15/side floor (edge of reliability; CI will be wide).
* ``triage_dimensions`` are persisted on 0 kept rows (179 trashed + 75 selected)
  → dimension-vs-LABEL AUC is NOT MEASURABLE (kept class empty); dimension-vs-
  PROXY is measurable on the 179+75 rows that carry them.

Firewall + bootstrap CIs mirror eval_slate_blend/eval_goodpaper_correlation
(mean + never one number). Reuses eval_slate_blend's pure metric helpers by file
path. Reads ``data/`` read-only, writes nothing. LIGHT — scipy + a corpus-embed
pass for the goal_sim comparison arm only (the MiniLM corpus model; no local LLM).

Usage (repo root, corpus model available for the goal_sim arm):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_triage_score_calibration.py
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

# Reuse eval_slate_blend's measurement apparatus verbatim (load by file path
# so importing it does NOT trigger its deferred heavy imports — main defers).
_SLATE = importlib.util.spec_from_file_location(
    "eval_slate_blend",
    Path(__file__).resolve().parent / "eval_slate_blend.py",
)
_ev = importlib.util.module_from_spec(_SLATE)
_SLATE.loader.exec_module(_ev)
_auc = _ev._auc
_p_at = _ev._p_at
_bootstrap_ci = _ev._bootstrap_ci
_fmt = _ev._fmt
# Reuse the good-paper correlation helpers (Spearman + bootstrap rho CI) too.
_GP = importlib.util.spec_from_file_location(
    "eval_goodpaper_correlation",
    Path(__file__).resolve().parent / "eval_goodpaper_correlation.py",
)
_gp = importlib.util.module_from_spec(_GP)
_GP.loader.exec_module(_gp)
_spearman = _gp._spearman
_bootstrap_rho_ci = _gp._bootstrap_rho_ci

# User-driven labels ONLY — the firewall (never the allocator's own selections).
KEPT = ("user_approved",)
TRASHED = ("user_rejected",)
MIN_PER_SIDE = 15

# The 5 LLM-extracted dimensions (abstract-based judgment at triage time).
DIMS = ("goal_alignment", "novelty_for_goals", "methodological_rigor",
        "actionability", "evidence_strength")
# Good-paper proxies for the dimension-vs-proxy arm.
PROXIES = ("max_author_h_index", "cited_by_count", "citation_percentile")


def _summary(row: dict) -> dict:
    raw = (row.get("shap_contribs_json") or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw).get("summary") or {}
    except json.JSONDecodeError:
        return {}


def _aux(row: dict) -> dict:
    raw = (row.get("shap_contribs_json") or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw).get("aux_context") or {}
    except json.JSONDecodeError:
        return {}


def _report_auc(name: str, keys: list[float], labels: list[int]) -> None:
    if len(set(labels)) < 2:
        print(f"  {name:<28} NOT MEASURABLE (single-class: kept={sum(labels)} "
              f"trashed={len(labels)-sum(labels)})")
        return
    auc = _auc(keys, labels)
    lo, hi = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
    print(f"  {name:<28} AUC={auc:.3f}  95%CI={_fmt(lo, hi)}  P@10={_p_at(keys, labels, 10):.2f}")


def _report_rho(name: str, x: list[float], y: list[float]) -> None:
    rho = _spearman(x, y)
    if rho != rho:
        print(f"  {name:<42} n={len(x):<4} ρ=N/A (degenerate)")
        return
    lo, hi = _bootstrap_rho_ci(x, y)
    print(f"  {name:<42} n={len(x):<4} ρ={rho:+.3f}  95%CI={_fmt(lo, hi)}")


def main() -> None:
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.storage.corpus import EmbeddingCache
    import yaml

    settings_ = get_settings()
    config = GoalsConfig.model_validate(yaml.safe_load(settings_.config_path.read_text()))

    # ---- load firewalled labeled rows ----
    all_labels = (*KEPT, *TRASHED)
    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(all_labels))
        rows = [dict(r) for r in conn.execute(
            f"""SELECT id, title, abstract, decision, composite_score,
                       shap_contribs_json
                FROM processed_feed_items
                WHERE decision IN ({placeholders})""",
            all_labels,
        )]
    finally:
        conn.close()
    labels = [0 if r["decision"] in TRASHED else 1 for r in rows]
    n_kept, n_trashed = sum(labels), len(labels) - sum(labels)
    print(f"firewalled labels: kept(user_approved)={n_kept} trashed(user_rejected)={n_trashed}")

    summaries = [_summary(r) for r in rows]
    rel_scores = [s.get("relevance_score") for s in summaries]
    n_rel = sum(1 for r in rel_scores if r is not None)
    print(f"rows with LLM relevance_score: {n_rel}/{len(rows)}")
    dims_present = {d: sum(1 for s in summaries if (s.get("triage_dimensions") or {}).get(d) is not None)
                    for d in DIMS}
    print(f"rows with triage_dimensions: {dims_present}")

    # ---- ARM 1: LLM relevance_score vs labels (the core calibration) ----
    print("\n=== LLM relevance_score vs labels (firewalled AUC) ===")
    pairs = [(rs, lab) for rs, lab in zip(rel_scores, labels) if rs is not None]
    if pairs:
        keys = [float(p[0]) for p in pairs]
        labs = [p[1] for p in pairs]
        print(f"  scored subset: kept={sum(labs)} trashed={len(labs)-sum(labs)}")
        _report_auc("LLM relevance_score", keys, labs)
    else:
        print("  NOT MEASURABLE — no labeled rows carry relevance_score")

    # ---- comparison arms: composite_score (gate) + goal_sim floor ----
    print("\n=== comparison arms (same labeled cohort) ===")
    comp = [r["composite_score"] for r in rows]
    comp_pairs = [(c, lab) for c, lab in zip(comp, labels) if c is not None]
    if comp_pairs:
        _report_auc("composite_score (gate)",
                    [float(p[0]) for p in comp_pairs], [p[1] for p in comp_pairs])
    print(f"  goal_sim alone (title-only floor): AUC=0.755 95%CI=[0.646,0.853]  "
          f"(from eval_relevancy_titleonly, same 15 kept cohort)")

    # heavy: goal_sim for the SAME rows, for a like-for-like comparison
    cache = EmbeddingCache(settings_.corpus_db_path, config.corpus.embedding_model)
    goal: list[float | None] = []
    for i, r in enumerate(rows):
        _aff, sims = cache.affinity_and_goals(r["title"] or "", r["abstract"] or "")
        goal.append(max(sims.values()) if sims else None)
        if (i + 1) % 100 == 0:
            print(f"  embedded {i + 1}/{len(rows)}", file=sys.stderr)
    goal_pairs = [(g, lab) for g, lab in zip(goal, labels) if g is not None]
    if goal_pairs:
        _report_auc("goal_sim (this run)",
                    [float(p[0]) for p in goal_pairs], [p[1] for p in goal_pairs])

    # ---- ARM 2: does the LLM score agree with the gate? ----
    print("\n=== LLM relevance_score vs composite_score (review-vs-gate agreement) ===")
    sx, sy = [], []
    for rs, c in zip(rel_scores, comp):
        if rs is None or c is None:
            continue
        sx.append(float(rs))
        sy.append(float(c))
    _report_rho("relevance_score vs composite_score", sx, sy)

    # ---- ARM 3: per-dimension vs proxies + vs composite (the rigor-guess test) ----
    print("\n=== per-dimension vs composite_score + proxies (rigor-guess test) ===")
    print("  (methodological_rigor is abstract-based; near-chance vs proxies ⇒ receipt for Part B)")
    auxs = [_aux(r) for r in rows]
    for d in DIMS:
        dvals = [((s.get("triage_dimensions") or {}).get(d)) for s in summaries]
        # vs composite
        sx, sy = [], []
        for dv, c in zip(dvals, comp):
            if dv is None or c is None:
                continue
            sx.append(float(dv))
            sy.append(float(c))
        _report_rho(f"{d} vs composite_score", sx, sy)
        # vs each proxy
        for p in PROXIES:
            sx, sy = [], []
            for dv, a in zip(dvals, auxs):
                pv = a.get(p)
                if dv is None or pv is None or pv == "":
                    continue
                sx.append(float(dv))
                sy.append(float(pv))
            _report_rho(f"{d} vs {p}", sx, sy)


if __name__ == "__main__":
    main()
