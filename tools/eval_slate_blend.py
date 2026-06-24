"""Retrospective check: does the goal blend (and the deep-review QUALITY lift)
improve the SLATE ordering — measured on a FIREWALLED, joinable, CI'd ground truth?

The blend weights (0.4 goal / 0.15 prestige) were measured on the Library
blind-judge benchmark — a different surface than the slate. This script validates
the transfer on the slate's OWN ground truth, with the fixes a 10-expert review
demanded before it gates any weight:

* FIREWALL (no leakage). The positive class is user-driven labels ONLY
  (``user_approved`` kept vs ``user_rejected`` trashed). ``selected``/``black_swan``
  are the daily-select allocator's OWN outputs of the rank_score under test, so
  they are reported as a SEPARATE diagnostic arm — never the firewall.
* JOIN + SAMPLE pre-flight. Deep-review quality is keyed by the Zotero library
  item_key; a feed row keys on its GUID. We join via ``materialized_zotero_key``
  (the real library key written at materialization — pure SQL, no Zotero reader)
  and PRINT the reviewed∩labeled overlap and per-class counts BEFORE the expensive
  embedding pass. Below ``MIN_PER_SIDE`` the quality arm is declared NOT MEASURABLE.
* STATS. Bootstrap 95% CIs on AUC/P@10, n per arm, plus a within-band ranking
  metric (NDCG@10 over the reviewed subset) — a single AUC over the whole cohort
  is dominated by bonus-0 rows and is near-blind to the within-band reorder the
  quality bonus performs. Ship a lift only if its CI excludes the baseline.
* COUNTERFACTUAL. additive capped bonus vs a normalized 4th blend term — the
  rank-position-delta distribution decides which keeps reorder reach bounded.

Usage (from the repo root, with the corpus model available):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_slate_blend.py

Reads the real ``data/`` DBs via Settings (read-only); writes nothing. HEAVY
(embeds every labeled row through the corpus model) — run user-coordinated,
foreground, single instance (see the memory-safe-runs guidance).
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys

# User-driven labels ONLY — the firewall (never the allocator's own selections).
KEPT = ("user_approved",)
TRASHED = ("user_rejected",)
# Allocator outputs — reported as a SEPARATE diagnostic arm, never the firewall.
DIAGNOSTIC_KEPT = ("selected", "black_swan")
# Measurability floor for the reviewed∩labeled subset (per class). Below this the
# quality arm is noise — declare NOT MEASURABLE rather than rubber-stamp a magnitude.
MIN_PER_SIDE = 15
# Counterfactual normalized-quality term weight (the "adopt a 4th blend term" arm).
QUALITY_TERM_WEIGHT = 0.10


# --- pure metrics (unit-tested in tests/test_eval_blend.py; no heavy imports) ---

def _auc(keys: list[float], labels: list[int]) -> float:
    """P(a kept row outranks a trashed row); ties = 0.5. Raises if single-class."""
    pos = [k for k, y in zip(keys, labels) if y == 1]
    neg = [k for k, y in zip(keys, labels) if y == 0]
    if not pos or not neg:
        raise ValueError("AUC needs both kept and trashed rows")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _p_at(keys: list[float], labels: list[int], k: int) -> float:
    order = sorted(range(len(keys)), key=lambda i: -keys[i])[:k]
    return sum(labels[i] for i in order) / min(k, len(order))


def _ndcg_at(keys: list[float], gains: list[int], k: int) -> float:
    """NDCG@k of ``gains`` (0/1) under the ``keys`` order — a within-subset ranking
    metric (rank reviewed-kept above reviewed-trashed), unlike whole-cohort AUC."""
    def _dcg(order: list[int]) -> float:
        from math import log2
        return sum(gains[i] / log2(rank + 2) for rank, i in enumerate(order[:k]))
    ranked = sorted(range(len(keys)), key=lambda i: -keys[i])
    ideal = sorted(range(len(gains)), key=lambda i: -gains[i])
    idcg = _dcg(ideal)
    return _dcg(ranked) / idcg if idcg > 0 else 0.0


def _bootstrap_ci(
    keys: list[float],
    labels: list[int],
    metric,
    *,
    require_both_classes: bool,
    n_boot: int = 2000,
    seed: int = 12345,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap CI for ``metric(keys, labels)`` by resampling row
    indices with replacement. Degenerate single-class resamples are skipped for
    metrics that need both classes (AUC) — explicit balance check, not error-masking."""
    rng = random.Random(seed)
    n = len(keys)
    vals: list[float] = []
    for _ in range(n_boot):
        sample = [rng.randrange(n) for _ in range(n)]
        sl = [labels[i] for i in sample]
        if require_both_classes and len(set(sl)) < 2:
            continue
        sk = [keys[i] for i in sample]
        vals.append(metric(sk, sl))
    if not vals:
        raise ValueError("no valid bootstrap resamples (cohort too degenerate)")
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return lo, hi


def _rank_positions(keys: list[float]) -> list[int]:
    order = sorted(range(len(keys)), key=lambda i: -keys[i])
    pos = [0] * len(keys)
    for rank, i in enumerate(order):
        pos[i] = rank
    return pos


def _position_deltas(base: list[float], alt: list[float]) -> list[int]:
    """Per-row |rank change| between two orderings of the same cohort."""
    pb, pa = _rank_positions(base), _rank_positions(alt)
    return [abs(pb[i] - pa[i]) for i in range(len(base))]


def _band_crossings(base: list[float], alt: list[float], buckets: list[int]) -> int:
    """# of ordered pairs where ``alt`` lets a LOWER-bucket row overtake a
    HIGHER-bucket row that ``base`` had ahead — a proxy for crossing a displayed
    relevance band (buckets = integer composite score). Bounds reorder reach."""
    pb, pa = _rank_positions(base), _rank_positions(alt)
    n = len(base)
    crossed = 0
    for i in range(n):
        for j in range(n):
            if buckets[i] > buckets[j] and pb[i] < pb[j] and pa[i] > pa[j]:
                crossed += 1
    return crossed


def _norm_col(vals: list[float | None]) -> list[float]:
    """Min-max a column to [0,1]; absent (None) → median of known; degenerate → 0.5."""
    known = [v for v in vals if v is not None]
    if not known:
        return [0.0] * len(vals)
    lo, hi = min(known), max(known)
    med = sorted(known)[len(known) // 2]
    span = hi - lo
    return [
        ((med if v is None else v) - lo) / span if span > 0 else 0.5
        for v in vals
    ]


def _blend4(
    rel: list[float],
    goal: list[float | None],
    prestige: list[float | None],
    quality: list[float | None],
    *,
    goal_w: float,
    prestige_w: float,
    quality_w: float,
) -> list[float]:
    """Counterfactual ONLY: a normalized 4-term blend (quality as a real blend
    term, not a post-hoc additive). Mirrors rank_blend's per-cohort min-max +
    median-for-absent contract; kept LOCAL so the shipped pure blend is not
    changed before P2 decides additive-vs-normalized."""
    rn = _norm_col([float(r) for r in rel])
    gn = _norm_col(goal)
    pn = _norm_col(prestige)
    qn = _norm_col(quality)
    rel_w = 1.0 - goal_w - prestige_w - quality_w
    return [rel_w * rn[i] + goal_w * gn[i] + prestige_w * pn[i] + quality_w * qn[i]
            for i in range(len(rel))]


# --- join + IO boundary --------------------------------------------------------

def _citation_percentile(payload_json: str | None) -> float | None:
    raw = (payload_json or "").strip()
    if not raw:
        return None
    aux = (json.loads(raw).get("aux_context") or {})
    pct = aux.get("citation_percentile")
    return float(pct) if pct is not None else None


def _row_quality(row: dict, reviews: dict) -> dict:
    """Deep-review band/grade for a feed row via its ``materialized_zotero_key``
    (the library key written at materialization = the deep_reviews.json key). An
    unmaterialized / unreviewed row has no quality — the documented empty contract."""
    key = (row.get("materialized_zotero_key") or "").strip()
    if not key:
        return {}
    return (reviews.get(key) or {}).get("quality") or {}


def _fmt(lo: float, hi: float) -> str:
    return f"[{lo:.3f}, {hi:.3f}]"


def main() -> None:
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services.model.rank_blend import blend_scores, quality_bonus
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.storage.corpus import EmbeddingCache
    import yaml

    settings_ = get_settings()
    config = GoalsConfig.model_validate(yaml.safe_load(settings_.config_path.read_text()))

    # ---- load labeled rows (firewalled) ----
    all_labels = (*KEPT, *TRASHED)
    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(all_labels))
        cur = conn.execute(
            f"""SELECT id, title, abstract, decision, composite_score,
                       shap_contribs_json, materialized_zotero_key
                FROM processed_feed_items
                WHERE decision IN ({placeholders}) AND composite_score IS NOT NULL""",
            all_labels,
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    rows = [r for r in rows if (r["abstract"] or "").strip()]
    labels = [0 if r["decision"] in TRASHED else 1 for r in rows]
    n_kept, n_trashed = sum(labels), len(labels) - sum(labels)
    print(f"firewalled labels: kept(user_approved)={n_kept} trashed(user_rejected)={n_trashed} "
          f"[selected/black_swan EXCLUDED from the firewall]")
    if len(rows) < 20:
        raise SystemExit(f"only {len(rows)} firewalled labeled rows — too few to measure")

    # ---- JOIN + SAMPLE pre-flight (cheap; before the heavy embed) ----
    reviews = deep_review._read_all()
    quals = [_row_quality(r, reviews) for r in rows]
    reviewed = [i for i, q in enumerate(quals) if q]
    rev_kept = sum(labels[i] for i in reviewed)
    rev_trashed = len(reviewed) - rev_kept
    print(f"join: {len(reviewed)}/{len(rows)} labeled rows have a deep review "
          f"(via materialized_zotero_key) — reviewed∩kept={rev_kept} reviewed∩trashed={rev_trashed}")
    measurable = rev_kept >= MIN_PER_SIDE and rev_trashed >= MIN_PER_SIDE
    if not measurable:
        print(f"*** QUALITY ARM NOT MEASURABLE *** reviewed subset < {MIN_PER_SIDE}/side "
              f"(have {rev_kept}/{rev_trashed}). Ship quality DISPLAY-ONLY / capped-by-prior; "
              f"record this n as a rejected-option receipt. Blend arms below still valid.")

    # ---- heavy: embed each row's goal_sim (corpus model) ----
    cache = EmbeddingCache(settings_.corpus_db_path, config.corpus.embedding_model)
    composite = [float(r["composite_score"]) for r in rows]
    goal: list[float | None] = []
    for i, r in enumerate(rows):
        _aff, sims = cache.affinity_and_goals(r["title"] or "", r["abstract"] or "")
        goal.append(max(sims.values()) if sims else None)
        if (i + 1) % 50 == 0:
            print(f"  embedded {i + 1}/{len(rows)}", file=sys.stderr)
    prestige = [_citation_percentile(r["shap_contribs_json"]) for r in rows]
    n_goal = sum(1 for g in goal if g is not None)
    n_prest = sum(1 for p in prestige if p is not None)

    blended = blend_scores(composite, goal, prestige)
    grade_bonus = [quality_bonus(q.get("quality_band"), q.get("grade"), use_band=False) for q in quals]
    band_bonus = [quality_bonus(q.get("quality_band"), q.get("grade"), use_band=True) for q in quals]
    blend_grade = [blended[i] + grade_bonus[i] for i in range(len(rows))]
    blend_band = [blended[i] + band_bonus[i] for i in range(len(rows))]

    def report(name: str, keys: list[float]) -> None:
        auc = _auc(keys, labels)
        lo, hi = _bootstrap_ci(keys, labels, _auc, require_both_classes=True)
        print(f"{name:<22} AUC={auc:.3f} 95%CI={_fmt(lo, hi)}  P@10={_p_at(keys, labels, 10):.2f}")

    print(f"\nrows={len(rows)} goal_sim_present={n_goal} prestige_present={n_prest}")
    report("composite-only", composite)
    report("blend(0.4/0.15)", blended)
    report("blend+grade", blend_grade)
    report("blend+band", blend_band)
    goal_only = [g if g is not None else min(x for x in goal if x is not None) for g in goal]
    report("goal_sim alone", goal_only)

    # ---- within-reviewed-subset ranking metric (AUC's blind spot) ----
    if reviewed:
        rk = [blended[i] for i in reviewed]
        rg = [blend_grade[i] for i in reviewed]
        rb = [blend_band[i] for i in reviewed]
        rl = [labels[i] for i in reviewed]
        print(f"\nreviewed-subset NDCG@10 (n={len(reviewed)}): "
              f"blend={_ndcg_at(rk, rl, 10):.3f}  +grade={_ndcg_at(rg, rl, 10):.3f}  "
              f"+band={_ndcg_at(rb, rl, 10):.3f}")

    # ---- counterfactual: additive bonus vs a normalized 4th term ----
    band_num = [{"highlight": 1.0, "neutral": 0.5, "uncertain": 0.5, "flag": 0.0}.get(
        (q.get("quality_band") or "").lower()) for q in quals]
    normalized = _blend4(composite, goal, prestige, band_num,
                         goal_w=0.40, prestige_w=0.15, quality_w=QUALITY_TERM_WEIGHT)
    buckets = [int(c) for c in composite]
    add_d = _position_deltas(blended, blend_band)
    norm_d = _position_deltas(blended, normalized)
    print(f"\ncounterfactual reorder reach (vs blend baseline):")
    print(f"  additive  +band : max|Δrank|={max(add_d)} mean={sum(add_d)/len(add_d):.2f} "
          f"#moved>1={sum(1 for d in add_d if d > 1)} band-crossings={_band_crossings(blended, blend_band, buckets)}")
    print(f"  normalized term : max|Δrank|={max(norm_d)} mean={sum(norm_d)/len(norm_d):.2f} "
          f"#moved>1={sum(1 for d in norm_d if d > 1)} band-crossings={_band_crossings(blended, normalized, buckets)}")

    # ---- goal-decomposition: are user-kept papers buried below the reviewed top-K? ----
    top_k = config.quality_review.top_k
    order = sorted(range(len(rows)), key=lambda i: -blended[i])
    reviewed_ranks = [order.index(i) for i in reviewed] if reviewed else []
    cutoff = max(reviewed_ranks) if reviewed_ranks else top_k
    buried_kept = sum(1 for rank, i in enumerate(order) if rank > cutoff and labels[i] == 1)
    print(f"\npromote-from-below diagnostic: {buried_kept} user-KEPT papers rank below the "
          f"reviewed cutoff (rank>{cutoff}) — quality lift CANNOT surface these (coverage-K / "
          f"the 0.5 corpus-affinity weight are the only levers there).")


if __name__ == "__main__":
    main()
