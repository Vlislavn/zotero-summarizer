"""Sweep the abstract-gate similarity threshold (``corpus.similarity_threshold``,
a PROVISIONAL -0.30 with no harness until now) against the user's OWN labels.

THE KNOB. Before any LLM call, a feed item whose corpus-affinity score is BELOW
``corpus.similarity_threshold`` is fast-rejected (see
``services/triage/summarization.py``: ``affinity_score < similarity_threshold``).
So the gate KEEPS a row iff ``corpus_affinity >= threshold``. Sweeping the
threshold trades precision vs recall of that keep-decision. This harness measures
that trade-off on a firewalled ground truth and reports the F1-best threshold
(plus a precision-floored variant) with a bootstrap CI.

The design mirrors ``tools/eval_slate_blend.py`` (read it first):

* FIREWALL (no leakage). The positive class is user-driven labels ONLY —
  ``user_approved`` (kept) vs ``user_rejected`` (trashed) from
  ``processed_feed_items``. The allocator's own ``selected``/``black_swan``
  outputs are NEVER the firewall — they are downstream of the score under test.
* SCORE = the gate's actual input. We read the stored ``corpus_affinity`` column,
  which IS the ``affinity_score`` the runtime gate compares to the threshold — not
  a re-embed, so the sweep reflects exactly what the gate would have decided.
* STATS. Bootstrap 95% percentile CI on the F1 of the best threshold (resampling
  row indices with replacement). A measurability floor (``MIN_PER_SIDE``) guards
  against rubber-stamping a magnitude on too few labels per side.
* PURE CORE. ``sweep_threshold`` is a DB-free pure function (unit-tested in
  ``tests/test_eval_triage_threshold.py``); ``main`` only does the read-only IO.

Usage (from the repo root):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_triage_threshold.py

Reads the real ``data/`` triage DB via Settings (read-only); writes nothing.
Cheap (no embedding pass — the score is already stored), but still run
user-coordinated, foreground, single instance (see memory-safe-runs guidance).
"""
from __future__ import annotations

import random
import sqlite3
from time import perf_counter

# User-driven labels ONLY — the firewall (never the allocator's own selections).
KEPT = ("user_approved",)
TRASHED = ("user_rejected",)
# Measurability floor (per class). Below this the sweep is noise — declare NOT
# MEASURABLE rather than rubber-stamp a threshold magnitude.
MIN_PER_SIDE = 15
# Default candidate grid for the gate threshold. The knob is bounded [-1, 1]
# (models/config.py: ge=-1.0, le=1.0); a 0.05 grid is finer than the gate's own
# resolution and spans the realistic negative-affinity reject region.
DEFAULT_THRESHOLDS = [round(-1.0 + 0.05 * i, 2) for i in range(41)]  # -1.00 .. +1.00


# --- pure core (unit-tested; no DB, no heavy imports) --------------------------

def _prf(scores: list[float], labels: list[int], threshold: float) -> tuple[float, float, float, int]:
    """Precision/recall/F1/n_kept of the gate's KEEP decision at ``threshold``.

    The gate keeps a row iff ``score >= threshold`` (it fast-rejects only when
    ``score < threshold``). A "true positive" is a kept row the user ALSO kept
    (label == 1). With no kept rows precision is undefined → 0.0 (a threshold that
    keeps nothing has no precision to claim); with no user-kept rows recall is
    undefined → 0.0. F1 is 0.0 whenever precision or recall is 0.0.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must be the same length")
    tp = fp = fn = 0
    for s, y in zip(scores, labels):
        kept = s >= threshold
        if kept and y == 1:
            tp += 1
        elif kept and y == 0:
            fp += 1
        elif not kept and y == 1:
            fn += 1
    n_kept = tp + fp
    precision = tp / n_kept if n_kept else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1, n_kept


def sweep_threshold(
    scores: list[float], labels: list[int], thresholds: list[float]
) -> list[dict]:
    """Per-candidate-threshold precision/recall/F1/n_kept of the keep-decision
    (``score >= threshold``) vs the user's kept(1)/trashed(0) label.

    Pure and DB-free: the whole measurement apparatus is testable on synthetic
    arrays. Returns one dict per threshold (input order preserved):
    ``{threshold, precision, recall, f1, n_kept}``.
    """
    if not thresholds:
        raise ValueError("thresholds must be non-empty")
    out: list[dict] = []
    for t in thresholds:
        precision, recall, f1, n_kept = _prf(scores, labels, t)
        out.append({
            "threshold": float(t),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "n_kept": n_kept,
        })
    return out


def best_by_f1(sweep: list[dict]) -> dict:
    """The sweep row maximizing F1; ties broken by the HIGHER threshold (keep
    fewer rows = cheaper gate, fewer LLM calls). Raises on an empty sweep."""
    if not sweep:
        raise ValueError("empty sweep has no best threshold")
    return max(sweep, key=lambda r: (r["f1"], r["threshold"]))


def best_precision_floored(sweep: list[dict], precision_floor: float) -> dict | None:
    """The F1-best sweep row whose precision is >= ``precision_floor`` (a
    precision-conscious variant — don't waste LLM calls on rows the user trashes).
    ``None`` if no candidate clears the floor (an honest empty contract, not a
    silent fallback to the unconstrained best)."""
    eligible = [r for r in sweep if r["precision"] >= precision_floor]
    if not eligible:
        return None
    return max(eligible, key=lambda r: (r["f1"], r["threshold"]))


def _bootstrap_f1_ci(
    scores: list[float],
    labels: list[int],
    threshold: float,
    *,
    n_boot: int = 2000,
    seed: int = 12345,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap CI for the F1 at a FIXED ``threshold`` by resampling
    row indices with replacement. Degenerate resamples with no positive label are
    skipped (F1 is undefined there) — an explicit balance check, not error-masking.
    """
    rng = random.Random(seed)
    n = len(scores)
    if n == 0:
        raise ValueError("cannot bootstrap an empty cohort")
    vals: list[float] = []
    for _ in range(n_boot):
        sample = [rng.randrange(n) for _ in range(n)]
        sl = [labels[i] for i in sample]
        if sum(sl) == 0:
            continue
        ss = [scores[i] for i in sample]
        vals.append(_prf(ss, sl, threshold)[2])
    if not vals:
        raise ValueError("no valid bootstrap resamples (cohort too degenerate)")
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return lo, hi


# --- IO boundary (read-only on user data) --------------------------------------

def _load_pairs(triage_db_path) -> tuple[list[float], list[int]]:
    """Load firewalled (corpus_affinity, label) pairs from the triage DB, READ-ONLY.

    label = 1 for ``user_approved``, 0 for ``user_rejected``. Rows with a NULL
    ``corpus_affinity`` are dropped — the gate has no score to compare for them, so
    they are outside this knob's measurement.
    """
    all_labels = (*KEPT, *TRASHED)
    placeholders = ",".join("?" * len(all_labels))
    conn = sqlite3.connect(f"file:{triage_db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            f"""SELECT corpus_affinity, decision
                FROM processed_feed_items
                WHERE decision IN ({placeholders}) AND corpus_affinity IS NOT NULL""",
            all_labels,
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    scores = [float(r[0]) for r in rows]
    labels = [0 if r[1] in TRASHED else 1 for r in rows]
    return scores, labels


def _fmt_ci(lo: float, hi: float) -> str:
    return f"[{lo:.3f}, {hi:.3f}]"


def main() -> None:
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services._common import settings as get_settings
    import yaml

    started = perf_counter()
    settings_ = get_settings()
    config = GoalsConfig.model_validate(yaml.safe_load(settings_.config_path.read_text()))
    current = float(config.corpus.similarity_threshold)

    scores, labels = _load_pairs(settings_.triage_db_path)
    n_kept, n_trashed = sum(labels), len(labels) - sum(labels)
    print(
        f"firewalled labels: kept(user_approved)={n_kept} "
        f"trashed(user_rejected)={n_trashed} "
        f"[selected/black_swan EXCLUDED from the firewall]"
    )
    print(f"current corpus.similarity_threshold = {current:+.3f} (provisional)")

    if n_kept < MIN_PER_SIDE or n_trashed < MIN_PER_SIDE:
        print(
            f"*** NOT MEASURABLE *** need >= {MIN_PER_SIDE} labeled rows per side "
            f"(have kept={n_kept} trashed={n_trashed}). Keep the provisional "
            f"{current:+.3f}; record this n as a rejected-option receipt."
        )
        raise SystemExit(0)

    # Sweep the grid + always evaluate the live default so the report is anchored.
    grid = sorted({*DEFAULT_THRESHOLDS, round(current, 2)})
    sweep = sweep_threshold(scores, labels, grid)

    print(f"\nthreshold sweep (score=corpus_affinity, keep iff score >= threshold):")
    print(f"  {'thresh':>7}  {'prec':>5}  {'recall':>6}  {'F1':>5}  {'n_kept':>6}")
    for row in sweep:
        marker = "  <- current" if abs(row["threshold"] - current) < 1e-9 else ""
        print(
            f"  {row['threshold']:>+7.2f}  {row['precision']:>5.3f}  "
            f"{row['recall']:>6.3f}  {row['f1']:>5.3f}  {row['n_kept']:>6d}{marker}"
        )

    best = best_by_f1(sweep)
    lo, hi = _bootstrap_f1_ci(scores, labels, best["threshold"])
    print(
        f"\nF1-best threshold = {best['threshold']:+.2f}  "
        f"F1={best['f1']:.3f} 95%CI={_fmt_ci(lo, hi)}  "
        f"prec={best['precision']:.3f} recall={best['recall']:.3f} "
        f"n_kept={best['n_kept']}"
    )

    floor = 0.80
    floored = best_precision_floored(sweep, floor)
    if floored is None:
        print(
            f"precision-floored (>= {floor:.2f}): NONE clears the floor — the "
            f"score does not separate kept/trashed at that precision."
        )
    else:
        print(
            f"precision-floored (>= {floor:.2f}) threshold = "
            f"{floored['threshold']:+.2f}  F1={floored['f1']:.3f} "
            f"prec={floored['precision']:.3f} recall={floored['recall']:.3f} "
            f"n_kept={floored['n_kept']}"
        )

    cur_prf = _prf(scores, labels, current)
    print(
        f"\nlive default {current:+.3f}: prec={cur_prf[0]:.3f} "
        f"recall={cur_prf[1]:.3f} F1={cur_prf[2]:.3f} n_kept={cur_prf[3]}  "
        f"(F1 delta to best = {best['f1'] - cur_prf[2]:+.3f})"
    )
    print(f"\ndone in {perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()
