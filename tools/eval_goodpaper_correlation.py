"""Good-paper benchmark: is the deep-review GRADE a real quality signal, or
decoration that echoes citation/prestige proxies?

WHY THIS EXISTS. The deep-review ``grade`` (A/B/C/D) gates display + the
quality bonus on ranking, but it was never validated against objective
good-paper proxies. This harness answers the decisive question: does the grade
add signal BEYOND prestige/citations (partial correlation), or does it merely
track them (decoration)?

WHAT IT MEASURES (on the 112 reviewed∩materialized rows, 2026-06-28 DB):

* grade(4/3/2/1) vs each good-paper proxy metric — Spearman ρ + bootstrap 95% CI
    proxies: max_author_h_index, cited_by_count, venue_works_count,
             citation_percentile (sparse)
* quality_band(highlight>neutral>uncertain>flag) vs proxies — Spearman ρ
* confidence / coverage_fraction vs proxies — Spearman ρ (sanity: do the
    review's OWN self-assessment fields track external quality?)
* PARTIAL correlation: grade vs proxies controlling for max_author_h_index
    (the densest proxy) — the "beyond prestige" decisive test
* grade vs user labels (kept/trashed) — AUC, firewalled like eval_slate_blend

WHAT IT CANNOT MEASURE (declared, not faked):
* The 112 reviewed rows are ALL ``decision='selected'`` (allocator picks) — 0
  have a firewalled user label. So the grade-vs-labels AUC arm is NOT
  MEASURABLE today (printed as such, with the n=0 receipt). This is the same
  firewall gap eval_slate_blend hits (0/410 labeled rows reviewed).
* ``soundness``/``novelty``/etc. are populated on only 4/112 rows (legacy
  format) — not a reliable signal; grade + quality_band + confidence +
  coverage_fraction are the dense fields used.

DATA SHAPE caveat (printed, not hidden): the proxies are ZERO-INFLATED —
freshly triaged papers have median cited_by_count=0, median h_index=0. A weak
Spearman here is partly a floor effect (the proxies have not had time to
differentiate), NOT proof the grade is uncorrelated with true quality.

Firewall + bootstrap CIs mirror eval_slate_blend/eval_prestige_weight (mean +
median; never one number). Reuses eval_slate_blend's pure metric helpers by
file path. Reads ``data/`` read-only, writes nothing. LIGHT — no embedder, no
local LLM (only scipy on already-collected proxies).

Usage (repo root):

    uv run python tools/eval_goodpaper_correlation.py
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
_bootstrap_ci = _ev._bootstrap_ci
_norm_col = _ev._norm_col
_row_quality = _ev._row_quality
_fmt = _ev._fmt

# User-driven labels ONLY — the firewall (never the allocator's own selections).
KEPT = ("user_approved",)
TRASHED = ("user_rejected",)
# Per-class measurability floor for the label AUC arm.
MIN_PER_SIDE = 15

# grade → ordinal (A best). quality_band → ordinal (highlight best).
_GRADE_ORD = {"A": 4, "B": 3, "C": 2, "D": 1}
_BAND_ORD = {"highlight": 4, "neutral": 3, "uncertain": 2, "flag": 1}

# Good-paper proxy metrics collected upstream into aux_context (read-only here).
PROXIES = ("max_author_h_index", "cited_by_count", "venue_works_count", "citation_percentile")


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman ρ on two equal-length lists. Pure (scipy.stats.spearmanr under
    the hood). Returns nan for degenerate (constant) input — caller filters."""
    from scipy.stats import spearmanr
    if len(x) < 3 or len(set(x)) == 1 or len(set(y)) == 1:
        return float("nan")
    r = spearmanr(x, y).statistic
    return float(r) if r == r else float("nan")


def _bootstrap_rho_ci(
    x: list[float], y: list[float], *, n_boot: int = 2000, seed: int = 12345, alpha: float = 0.05
) -> tuple[float, float]:
    """Percentile bootstrap CI for Spearman ρ by resampling row indices with
    replacement. Degenerate (constant) resamples are skipped — explicit, not
    error-masking. Returns (nan, nan) if no valid resample survives."""
    rng_seed = seed
    import random as _r
    rng = _r.Random(rng_seed)
    n = len(x)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        sx = [x[i] for i in idx]
        sy = [y[i] for i in idx]
        if len(set(sx)) == 1 or len(set(sy)) == 1:
            continue
        from scipy.stats import spearmanr
        r = spearmanr(sx, sy).statistic
        if r == r:
            vals.append(float(r))
    if not vals:
        return float("nan"), float("nan")
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return lo, hi


def _partial_spearman(x: list[float], y: list[float], z: list[float]) -> float:
    """Partial Spearman: ρ(rank(x), rank(y)) controlling for rank(z), via the
    standard rank-residual formula. Pure (scipy). The "beyond prestige" test —
    does the grade carry signal orthogonal to the h-index proxy?"""
    import numpy as np
    from scipy.stats import spearmanr
    if len(x) < 4 or len(set(x)) == 1 or len(set(y)) == 1 or len(set(z)) == 1:
        return float("nan")
    rx = _ranks(x)
    ry = _ranks(y)
    rz = _ranks(z)
    rxz = _residual_against(rx, rz)
    ryz = _residual_against(ry, rz)
    if len(set(rxz)) == 1 or len(set(ryz)) == 1:
        return float("nan")
    return float(spearmanr(rxz, ryz).statistic)


def _ranks(v: list[float]) -> list[float]:
    """Average-rank transform (handles ties). Pure."""
    import numpy as np
    a = np.array(v, dtype=float)
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    # average ties
    seen: dict[float, list[int]] = {}
    for i, val in enumerate(a):
        seen.setdefault(float(val), []).append(i)
    for val, idxs in seen.items():
        if len(idxs) > 1:
            ranks[idxs] = float(np.mean(ranks[idxs]))
    return ranks.tolist()


def _residual_against(a: list[float], b: list[float]) -> list[float]:
    """Least-squares residual of ``a`` regressed on ``b`` (both already ranks).
    Pure (numpy lstsq). The orthogonalized component used in partial corr."""
    import numpy as np
    aa = np.array(a, dtype=float)
    bb = np.array(b, dtype=float)
    # fit aa = slope*bb + intercept
    X = np.column_stack([bb, np.ones_like(bb)])
    coef, *_ = np.linalg.lstsq(X, aa, rcond=None)
    return (aa - X @ coef).tolist()


def _median(v: list[float]) -> float:
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _aux(row: dict) -> dict:
    raw = (row.get("shap_contribs_json") or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw).get("aux_context") or {}
    except json.JSONDecodeError:
        return {}


def _grade_ord(q: dict) -> float | None:
    g = q.get("grade")
    return _GRADE_ORD.get(g) if g else None


def _band_ord(q: dict) -> float | None:
    b = (q.get("quality_band") or "").lower()
    return _BAND_ORD.get(b) if b else None


def _report_corr(name: str, x: list[float], y: list[float]) -> None:
    """Spearman ρ mean + median + bootstrap 95% CI for x vs y (paired, no NaN)."""
    n = len(x)
    rho = _spearman(x, y)
    lo, hi = _bootstrap_rho_ci(x, y)
    # median of bootstrap draws (robust central tendency, mirrors "never one number")
    if rho != rho:
        print(f"  {name:<42} n={n:<4} ρ=N/A (degenerate — constant signal)")
        return
    print(f"  {name:<42} n={n:<4} ρ={rho:+.3f}  95%CI={_fmt(lo, hi)}")


def main() -> None:
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.library import deep_review

    settings_ = get_settings()
    reviews = deep_review._read_all()
    print(f"cached deep reviews: {len(reviews)}")

    # ---- join reviewed rows to feed rows via materialized_zotero_key ----
    graded_keys = [k for k, v in reviews.items()
                   if (v.get("quality") or {}).get("grade") in _GRADE_ORD]
    print(f"reviews with a real grade A/B/C/D: {len(graded_keys)}")
    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        ph = ",".join("?" * len(graded_keys))
        rows = [dict(r) for r in conn.execute(
            f"""SELECT materialized_zotero_key, decision, composite_score,
                       shap_contribs_json
                FROM processed_feed_items
                WHERE materialized_zotero_key IN ({ph})""",
            graded_keys,
        )]
    finally:
        conn.close()
    print(f"graded reviews joinable to a feed row: {len(rows)}")
    if not rows:
        raise SystemExit("no joinable rows — benchmark not measurable on this DB")

    from collections import Counter
    print(f"  by decision: {dict(Counter(r['decision'] for r in rows))}")

    # ---- assemble the paired cohort: grade/band/conf/coverage × proxies ----
    quals = [_row_quality(r, reviews) for r in rows]
    grades = [_grade_ord(q) for q in quals]
    bands = [_band_ord(q) for q in quals]
    confs = [q.get("confidence") for q in quals]
    covs = [q.get("coverage_fraction") for q in quals]
    auxs = [_aux(r) for r in rows]
    proxy_cols = {p: [a.get(p) for a in auxs] for p in PROXIES}

    # population receipts (no silent caps)
    print("\nfield population (/" + str(len(rows)) + "):")
    print(f"  grade={sum(1 for g in grades if g is not None)}  "
          f"quality_band={sum(1 for b in bands if b is not None)}  "
          f"confidence={sum(1 for c in confs if c not in (None,''))}  "
          f"coverage_fraction={sum(1 for c in covs if c not in (None,''))}")
    for p in PROXIES:
        nn = sum(1 for v in proxy_cols[p] if v not in (None, ""))
        print(f"  proxy {p}: {nn} non-null")

    def _paired(sig: list, proxy_name: str) -> tuple[list[float], list[float]]:
        sx, sy = [], []
        pv = proxy_cols[proxy_name]
        for s, p in zip(sig, pv):
            if s is None or p is None or p == "":
                continue
            sx.append(float(s))
            sy.append(float(p))
        return sx, sy

    # ---- grade vs proxies (the core "does the grade track good-paper signals") ----
    print("\n=== grade(A=4..D=1) vs good-paper proxies — Spearman ρ ===")
    for p in PROXIES:
        sx, sy = _paired(grades, p)
        _report_corr(f"grade vs {p}", sx, sy)

    # ---- quality_band vs proxies ----
    print("\n=== quality_band(highlight=4..flag=1) vs proxies — Spearman ρ ===")
    for p in PROXIES:
        sx, sy = _paired(bands, p)
        _report_corr(f"band vs {p}", sx, sy)

    # ---- self-assessment sanity: do the review's own conf/coverage track proxies? ----
    print("\n=== review self-assessment vs proxies (sanity) ===")
    for p in PROXIES:
        sx, sy = _paired(confs, p)
        _report_corr(f"confidence vs {p}", sx, sy)
        sx, sy = _paired(covs, p)
        _report_corr(f"coverage_fraction vs {p}", sx, sy)

    # ---- the decisive test: grade BEYOND prestige (partial correlation) ----
    # control for the densest proxy (max_author_h_index); requires paired non-null.
    print("\n=== DECISIVE: grade vs proxies CONTROLLING for max_author_h_index ===")
    ctrl = proxy_cols["max_author_h_index"]
    for p in PROXIES:
        if p == "max_author_h_index":
            continue
        gx, px, cx = [], [], []
        pv = proxy_cols[p]
        for g, pp, c in zip(grades, pv, ctrl):
            if g is None or pp is None or pp == "" or c is None or c == "":
                continue
            gx.append(float(g))
            px.append(float(pp))
            cx.append(float(c))
        if len(gx) < 4:
            print(f"  grade vs {p} | h_index: n={len(gx)} too small")
            continue
        pr = _partial_spearman(gx, px, cx)
        bare = _spearman(gx, px)
        pr_s = f"{pr:+.3f}" if pr == pr else "N/A"
        bare_s = f"{bare:+.3f}" if bare == bare else "N/A"
        print(f"  grade vs {p:<20} n={len(gx):<4} ρ={bare_s}  →  partial|ρ(h_index)={pr_s}")

    # ---- grade vs the gate's OWN composite_score (does the review agree with triage?) ----
    print("\n=== grade vs triage composite_score (review-vs-gate agreement) ===")
    sx, sy = [], []
    for g, r in zip(grades, rows):
        c = r.get("composite_score")
        if g is None or c is None:
            continue
        sx.append(float(g))
        sy.append(float(c))
    _report_corr("grade vs composite_score", sx, sy)

    # ---- grade vs user labels (firewalled AUC) — declared NOT MEASURABLE if 0 ----
    print("\n=== grade vs user labels (firewalled AUC) ===")
    labeled = [r for r in rows if r["decision"] in (*KEPT, *TRASHED)]
    n_kept = sum(1 for r in labeled if r["decision"] in KEPT)
    n_trashed = sum(1 for r in labeled if r["decision"] in TRASHED)
    print(f"  firewalled-labeled reviewed rows: kept={n_kept} trashed={n_trashed}")
    if n_kept < MIN_PER_SIDE or n_trashed < MIN_PER_SIDE:
        print(f"  *** GRADE-vs-LABELS AUC NOT MEASURABLE *** < {MIN_PER_SIDE}/side "
              f"(have {n_kept}/{n_trashed}). All {len(rows)} reviewed rows are "
              f"allocator 'selected' picks — 0 carry a firewalled user verdict. "
              f"Record n=0 as the receipt; grade gating stays display-only until "
              f"enough reviewed picks accumulate user labels.")
    else:
        keys = [g for g, r in zip(grades, rows) if g is not None and r["decision"] in (*KEPT, *TRASHED)]
        labels = [1 if r["decision"] in KEPT else 0 for r, g in zip(rows, grades)
                  if g is not None and r["decision"] in (*KEPT, *TRASHED)]
        auc = _auc(keys, labels)
        lo, hi = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
        print(f"  grade-vs-labels AUC={auc:.3f} 95%CI={_fmt(lo, hi)}")


if __name__ == "__main__":
    main()
