"""Ablation for the PROVISIONAL ``PRESTIGE_BLEND_WEIGHT`` knob (default 0.15).

The composite re-rank blends three cohort-normalized signals — gate relevance,
goal-text similarity (weight ``GOAL_BLEND_WEIGHT = 0.4``) and prestige (weight
``w``, the knob under test). ``GOAL_BLEND_WEIGHT`` is blind-judge measured;
``PRESTIGE_BLEND_WEIGHT`` is hand-set and marked PROVISIONAL in
``services/model/rank_blend.py`` — no ablation exists. This harness supplies one.

WHAT IT MEASURES: holding the goal weight fixed, it sweeps the prestige weight
``w ∈ {0.0, 0.05, 0.10, 0.15, 0.20, 0.30}`` and, for each, recomputes the blend
on the user's OWN firewalled kept/trashed labels, scoring AUC (P(kept outranks
trashed)), P@10 and NDCG@10 with bootstrap 95% CIs. It reports the ``w`` that
maximizes NDCG@10 and whether the current default 0.15 falls inside that best
weight's CI — i.e. whether keeping 0.15 is justified by the user's own data.

Discipline mirrors ``tools/eval_slate_blend.py`` (NOT edited — its pure metric
helpers are imported by file path so this stays a single source of truth):

* FIREWALL. Positive class = user-driven labels only (``user_approved`` kept vs
  ``user_rejected`` trashed); the allocator's own ``selected``/``black_swan``
  outputs of the score under test are NEVER the ground truth.
* FLOOR-GUARD. Below ``MIN_PER_SIDE`` per class the sweep is NOT MEASURABLE —
  declared, not rubber-stamped.
* STATS. Percentile bootstrap 95% CIs on every metric; the default is justified
  only if 0.15's NDCG CI overlaps the best weight's.

Usage (from the repo root, with the corpus model available):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_prestige_weight.py

Reads the real ``data/`` DBs via Settings (read-only); writes nothing. HEAVY
(embeds every labeled row through the corpus model) — run user-coordinated,
foreground, single instance (see the memory-safe-runs guidance).
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from time import perf_counter

# Reuse eval_slate_blend's measurement apparatus verbatim (load by file path so
# importing it does NOT trigger its deferred heavy imports — main() defers them).
_SLATE = importlib.util.spec_from_file_location(
    "eval_slate_blend",
    Path(__file__).resolve().parent / "eval_slate_blend.py",
)
_ev = importlib.util.module_from_spec(_SLATE)
_SLATE.loader.exec_module(_ev)
_auc = _ev._auc
_p_at = _ev._p_at
_ndcg_at = _ev._ndcg_at
_bootstrap_ci = _ev._bootstrap_ci

# User-driven labels ONLY — the firewall (never the allocator's own selections).
KEPT = ("user_approved",)
TRASHED = ("user_rejected",)
# Per-class measurability floor; below this the sweep is noise, not a verdict.
MIN_PER_SIDE = 15
# The weights swept for the prestige term. The goal weight is held FIXED at its
# blind-judge-measured value so this isolates the prestige knob alone.
PRESTIGE_WEIGHTS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30)
DEFAULT_PRESTIGE_WEIGHT = 0.15


def ablate_prestige_weight(rows: list[dict], weights: list[float]) -> list[dict]:
    """Pure ablation: re-rank ``rows`` at each prestige weight and score them.

    Each ``row`` carries the component scores the blend consumes:
      * ``base``      — gate relevance score (required, float).
      * ``goal_sim``  — goal-text similarity, or ``None`` if absent for the row.
      * ``prestige``  — prestige signal (e.g. citation percentile), or ``None``.
      * ``label``     — 1 = user-kept, 0 = user-trashed.

    For each weight ``w`` the blend is recomputed via ``rank_blend.blend_scores``
    with the goal weight held at ``GOAL_BLEND_WEIGHT`` and the prestige weight set
    to ``w`` (so this isolates the prestige knob), then the firewalled labels are
    scored. Returns one dict per weight:
    ``{weight, auc, p_at_10, ndcg_at_10, n}``. No I/O — fully unit-testable.
    """
    from zotero_summarizer.services.model.rank_blend import (
        GOAL_BLEND_WEIGHT,
        blend_scores,
    )

    if not rows:
        raise ValueError("ablate_prestige_weight needs at least one row")
    labels = [int(r["label"]) for r in rows]
    if set(labels) - {0, 1}:
        raise ValueError("labels must be 0 (trashed) or 1 (kept)")
    base = [float(r["base"]) for r in rows]
    goal = [r["goal_sim"] for r in rows]
    prestige = [r["prestige"] for r in rows]
    n = len(rows)

    out: list[dict] = []
    for w in weights:
        keys = blend_scores(
            base, goal, prestige,
            goal_weight=GOAL_BLEND_WEIGHT, prestige_weight=float(w),
        )
        out.append({
            "weight": float(w),
            "auc": _auc(keys, labels),
            "p_at_10": _p_at(keys, labels, 10),
            "ndcg_at_10": _ndcg_at(keys, labels, 10),
            "n": n,
        })
    return out


def _citation_percentile(payload_json: str | None) -> float | None:
    """Prestige signal: the OpenAlex citation percentile in the SHAP payload's
    ``aux_context`` — the same known-evidence prestige term the blend consumes
    (mirrors eval_slate_blend's extractor; absent ⇒ None, never a relevance fallback)."""
    raw = (payload_json or "").strip()
    if not raw:
        return None
    aux = (json.loads(raw).get("aux_context") or {})
    pct = aux.get("citation_percentile")
    return float(pct) if pct is not None else None


def _fmt(lo: float, hi: float) -> str:
    return f"[{lo:.3f}, {hi:.3f}]"


def _load_rows() -> list[dict]:
    """Read firewalled labeled rows + their component scores from the real DBs
    (read-only). Mirrors eval_slate_blend: gate=composite_score, prestige=citation
    percentile, goal_sim=corpus-model max goal similarity over the cohort."""
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
    raw = [r for r in raw if (r["abstract"] or "").strip()]
    if len(raw) < 2 * MIN_PER_SIDE:
        raise SystemExit(
            f"only {len(raw)} firewalled labeled rows — need ≥{2 * MIN_PER_SIDE} to ablate"
        )

    cache = EmbeddingCache(settings_.corpus_db_path, config.corpus.embedding_model)
    rows: list[dict] = []
    for i, r in enumerate(raw):
        _aff, sims = cache.affinity_and_goals(r["title"] or "", r["abstract"] or "")
        rows.append({
            "base": float(r["composite_score"]),
            "goal_sim": (max(sims.values()) if sims else None),
            "prestige": _citation_percentile(r["shap_contribs_json"]),
            "label": 0 if r["decision"] in TRASHED else 1,
        })
        if (i + 1) % 50 == 0:
            print(f"  embedded {i + 1}/{len(raw)}", file=sys.stderr)
    return rows


def main() -> None:
    t0 = perf_counter()
    rows = _load_rows()
    labels = [r["label"] for r in rows]
    n_kept, n_trashed = sum(labels), len(labels) - sum(labels)
    n_goal = sum(1 for r in rows if r["goal_sim"] is not None)
    n_prest = sum(1 for r in rows if r["prestige"] is not None)
    print(
        f"firewalled labels: kept(user_approved)={n_kept} trashed(user_rejected)={n_trashed} "
        f"[selected/black_swan EXCLUDED]  goal_sim_present={n_goal} prestige_present={n_prest}"
    )
    if n_kept < MIN_PER_SIDE or n_trashed < MIN_PER_SIDE:
        print(
            f"*** PRESTIGE SWEEP NOT MEASURABLE *** need ≥{MIN_PER_SIDE}/side "
            f"(have {n_kept}/{n_trashed}) — keep 0.15 by prior, record this n as a receipt."
        )
        return
    if n_prest == 0:
        print(
            "*** PRESTIGE SWEEP NOT MEASURABLE *** no row carries a prestige signal, so the "
            "weight cannot move the order — keep 0.15 by prior, record this as a receipt."
        )
        return

    results = ablate_prestige_weight(rows, list(PRESTIGE_WEIGHTS))

    # Re-derive the blended keys per weight to attach bootstrap CIs (the pure
    # function returns point estimates; CIs need the resampled keys/labels).
    from zotero_summarizer.services.model.rank_blend import GOAL_BLEND_WEIGHT, blend_scores
    base = [r["base"] for r in rows]
    goal = [r["goal_sim"] for r in rows]
    prestige = [r["prestige"] for r in rows]

    print(f"\nprestige-weight sweep (goal_weight fixed at {GOAL_BLEND_WEIGHT}, n={len(rows)}):")
    ci_by_w: dict[float, tuple[float, float]] = {}
    for res in results:
        w = res["weight"]
        keys = blend_scores(base, goal, prestige,
                            goal_weight=GOAL_BLEND_WEIGHT, prestige_weight=w)
        auc_ci = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
        ndcg_ci = _bootstrap_ci(
            keys, labels, lambda k, l: _ndcg_at(k, l, 10), require_both_classes=False
        )
        ci_by_w[w] = ndcg_ci
        flag = "  <- default" if w == DEFAULT_PRESTIGE_WEIGHT else ""
        print(
            f"  w={w:<5} AUC={res['auc']:.3f} {_fmt(*auc_ci)}  "
            f"P@10={res['p_at_10']:.2f}  NDCG@10={res['ndcg_at_10']:.3f} {_fmt(*ndcg_ci)}{flag}"
        )

    best = max(results, key=lambda r: r["ndcg_at_10"])
    best_w = best["weight"]
    best_lo, best_hi = ci_by_w[best_w]
    default_res = next(r for r in results if r["weight"] == DEFAULT_PRESTIGE_WEIGHT)
    default_ndcg = default_res["ndcg_at_10"]
    justified = best_lo <= default_ndcg <= best_hi

    print(
        f"\nNDCG-best prestige weight: w={best_w} (NDCG@10={best['ndcg_at_10']:.3f} "
        f"{_fmt(best_lo, best_hi)})"
    )
    if best_w == DEFAULT_PRESTIGE_WEIGHT:
        print(f"VERDICT: the current default 0.15 IS the NDCG-best weight — keep it.")
    elif justified:
        print(
            f"VERDICT: default 0.15 (NDCG@10={default_ndcg:.3f}) falls INSIDE the best weight's "
            f"95% CI {_fmt(best_lo, best_hi)} — the data does not justify changing it; keep 0.15."
        )
    else:
        print(
            f"VERDICT: default 0.15 (NDCG@10={default_ndcg:.3f}) is OUTSIDE the best weight's "
            f"95% CI {_fmt(best_lo, best_hi)} — w={best_w} is a justified candidate; ship a lift "
            f"only if a held-out re-run confirms (do not over-fit this single cohort)."
        )
    print(f"\n(elapsed {perf_counter() - t0:.1f}s)")


if __name__ == "__main__":
    main()
