"""Relevancy-knob measurement on a SYMMETRIC title-only goal_sim signal.

WHY THIS EXISTS. ``eval_slate_blend`` / ``eval_prestige_weight`` filter labeled
rows to those with a non-empty ``abstract`` — but EVERY ``user_approved`` row in
the current DB was approved at the title/collection stage and carries NO
abstract. The filter therefore drops the entire positive class → single-class →
AUC crash. The quality arm is also unmeasurable (0/410 labeled rows have a deep
review joined via ``materialized_zotero_key``).

Computing goal_sim title-only for KEPT rows but full (title+abstract) for
TRASHED rows would CONFOUND the measurement: the abstract shifts goal_sim in
both directions (probed +0.20 / -0.11 on real rows), so any kept-vs-trashed
gap would mix the blend-weight effect with the signal-fidelity effect.

This harness removes the confound by computing goal_sim **title-only for BOTH
classes** — a symmetric, degraded-but-fair signal. It then runs the prestige-
weight sweep (the PROVISIONAL ``PRESTIGE_BLEND_WEIGHT=0.15`` knob) and the
blend ablation on that symmetric signal. The number is honest about being a
title-only floor: a lift here is a lower bound on the lift the full signal
would show; a null result on title-only does NOT refute the blend (the full
signal is stronger — blind-judge Spearman 0.72).

Firewall + floor + stats mirror ``eval_prestige_weight`` (user-driven labels
ONLY; ≥15/side; bootstrap 95% CIs). Reuses its pure ``ablate_prestige_weight``.

Usage (repo root; reads data/ read-only, writes nothing):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_relevancy_titleonly.py
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from time import perf_counter

# Reuse eval_prestige_weight's measurement apparatus (load by file path so
# importing it does NOT trigger its deferred heavy imports).
_PW = importlib.util.spec_from_file_location(
    "eval_prestige_weight",
    Path(__file__).resolve().parent / "eval_prestige_weight.py",
)
_pw = importlib.util.module_from_spec(_PW)
_PW.loader.exec_module(_pw)
ablate_prestige_weight = _pw.ablate_prestige_weight
_auc = _pw._auc
_p_at = _pw._p_at
_ndcg_at = _pw._ndcg_at
_bootstrap_ci = _pw._bootstrap_ci
_citation_percentile = _pw._citation_percentile

# Reuse the slate harness's blend-arm reporter + counterfactual helpers.
_SLATE = importlib.util.spec_from_file_location(
    "eval_slate_blend",
    Path(__file__).resolve().parent / "eval_slate_blend.py",
)
_ev = importlib.util.module_from_spec(_SLATE)
_SLATE.loader.exec_module(_ev)

KEPT = ("user_approved",)
TRASHED = ("user_rejected",)
MIN_PER_SIDE = 15
DEFAULT_PRESTIGE_WEIGHT = 0.15


def _load_rows_titleonly() -> list[dict]:
    """Read firewalled labeled rows; goal_sim is computed TITLE-ONLY for every
    row (symmetric — no abstract confound across classes). Kept rows lack
    abstracts; trashed rows have them, but we deliberately ignore the abstract
    here so the signal is comparable across classes."""
    import yaml

    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.storage.corpus import EmbeddingCache

    settings_ = get_settings()
    config = GoalsConfig.model_validate(yaml.safe_load(settings_.config_path.read_text()))

    all_labels = (*KEPT, *TRASHED)
    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(all_labels))
        cur = conn.execute(
            f"""SELECT id, title, abstract, decision, composite_score, shap_contribs_json
                FROM processed_feed_items
                WHERE decision IN ({placeholders}) AND composite_score IS NOT NULL""",
            all_labels,
        )
        raw = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    # Require a title (the goal_sim input); the abstract is intentionally unused.
    raw = [r for r in raw if (r["title"] or "").strip()]
    if len(raw) < 2 * MIN_PER_SIDE:
        raise SystemExit(
            f"only {len(raw)} firewalled labeled rows with titles — need ≥{2 * MIN_PER_SIDE}"
        )

    cache = EmbeddingCache(settings_.corpus_db_path, config.corpus.embedding_model)
    rows: list[dict] = []
    for i, r in enumerate(raw):
        # TITLE-ONLY: pass "" for the abstract on every row, both classes.
        _aff, sims = cache.affinity_and_goals(r["title"] or "", "")
        rows.append({
            "base": float(r["composite_score"]),
            "goal_sim": (max(sims.values()) if sims else None),
            "prestige": _citation_percentile(r["shap_contribs_json"]),
            "label": 0 if r["decision"] in TRASHED else 1,
        })
        if (i + 1) % 50 == 0:
            print(f"  embedded {i + 1}/{len(raw)} (title-only)", file=sys.stderr)
    return rows


def _fmt(lo: float, hi: float) -> str:
    return f"[{lo:.3f}, {hi:.3f}]"


def main() -> None:
    t0 = perf_counter()
    rows = _load_rows_titleonly()
    labels = [r["label"] for r in rows]
    n_kept, n_trashed = sum(labels), len(labels) - sum(labels)
    n_goal = sum(1 for r in rows if r["goal_sim"] is not None)
    n_prest = sum(1 for r in rows if r["prestige"] is not None)
    print(
        f"firewalled labels (TITLE-ONLY goal_sim): kept(user_approved)={n_kept} "
        f"trashed(user_rejected)={n_trashed}  goal_sim_present={n_goal} prestige_present={n_prest}"
    )
    if n_kept < MIN_PER_SIDE or n_trashed < MIN_PER_SIDE:
        print(
            f"*** NOT MEASURABLE *** need ≥{MIN_PER_SIDE}/side (have {n_kept}/{n_trashed}) — "
            f"record this n as a receipt; keep defaults by prior."
        )
        return

    from zotero_summarizer.services.model.rank_blend import (
        GOAL_BLEND_WEIGHT,
        PRESTIGE_BLEND_WEIGHT,
        blend_scores,
    )

    base = [r["base"] for r in rows]
    goal = [r["goal_sim"] for r in rows]
    prestige = [r["prestige"] for r in rows]

    # --- blend arms (composite-only vs blend vs goal-only) ---
    blended = blend_scores(base, goal, prestige)
    goal_only = [g if g is not None else 0.0 for g in goal]
    print(f"\nblend arms (title-only goal_sim, n={len(rows)}):")
    for name, keys in (("composite-only", base), ("blend(0.4/0.15)", blended), ("goal_sim alone", goal_only)):
        auc = _auc(keys, labels)
        lo, hi = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
        print(f"  {name:<18} AUC={auc:.3f} 95%CI={_fmt(lo, hi)}  P@10={_p_at(keys, labels, 10):.2f}")

    # --- prestige-weight sweep (the PROVISIONAL knob) ---
    results = ablate_prestige_weight(rows, list(_pw.PRESTIGE_WEIGHTS))
    print(f"\nprestige-weight sweep (goal_weight fixed at {GOAL_BLEND_WEIGHT}, title-only, n={len(rows)}):")
    ci_by_w: dict[float, tuple[float, float]] = {}
    for res in results:
        w = res["weight"]
        keys = blend_scores(base, goal, prestige, goal_weight=GOAL_BLEND_WEIGHT, prestige_weight=w)
        auc_ci = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
        ndcg_ci = _bootstrap_ci(keys, labels, lambda k, l: _ndcg_at(k, l, 10), require_both_classes=False)
        ci_by_w[w] = ndcg_ci
        flag = "  <- default" if w == DEFAULT_PRESTIGE_WEIGHT else ""
        print(f"  w={w:<5} AUC={res['auc']:.3f} {_fmt(*auc_ci)}  P@10={res['p_at_10']:.2f}  "
              f"NDCG@10={res['ndcg_at_10']:.3f} {_fmt(*ndcg_ci)}{flag}")

    best = max(results, key=lambda r: r["ndcg_at_10"])
    best_w = best["weight"]
    best_lo, best_hi = ci_by_w[best_w]
    default_res = next(r for r in results if r["weight"] == DEFAULT_PRESTIGE_WEIGHT)
    default_ndcg = default_res["ndcg_at_10"]
    justified = best_lo <= default_ndcg <= best_hi
    print(f"\nNDCG-best prestige weight: w={best_w} (NDCG@10={best['ndcg_at_10']:.3f} {_fmt(best_lo, best_hi)})")
    if best_w == DEFAULT_PRESTIGE_WEIGHT:
        print(f"VERDICT: default 0.15 IS the NDCG-best weight — keep it (title-only floor confirms).")
    elif justified:
        print(f"VERDICT: default 0.15 (NDCG@10={default_ndcg:.3f}) falls INSIDE the best weight's "
              f"95% CI {_fmt(best_lo, best_hi)} — keep 0.15 (title-only; full-signal re-run may sharpen).")
    else:
        print(f"VERDICT: default 0.15 OUTSIDE best CI — w={best_w} a candidate; CONFIRM on full-signal "
              f"before shipping (title-only is a degraded floor, do not over-fit).")

    # --- goal-weight confirmation on the symmetric signal ---
    print(f"\ngoal-weight check (prestige fixed at {PRESTIGE_BLEND_WEIGHT}, title-only):")
    for gw in (0.0, 0.2, 0.4, 0.6):
        keys = blend_scores(base, goal, prestige, goal_weight=gw, prestige_weight=PRESTIGE_BLEND_WEIGHT)
        auc = _auc(keys, labels)
        lo, hi = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
        flag = "  <- default" if gw == GOAL_BLEND_WEIGHT else ""
        print(f"  goal_w={gw:<4} AUC={auc:.3f} 95%CI={_fmt(lo, hi)}{flag}")

    print(f"\n(elapsed {perf_counter() - t0:.1f}s)  [title-only goal_sim — a symmetric floor; "
          f"full-signal re-run needed before flipping any default.]")


if __name__ == "__main__":
    main()
