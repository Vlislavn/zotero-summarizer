"""P2 label-free replay: the directive's acceptance metric for the quality-first re-rank.

Groups historical scored feed rows into daily cohorts and compares the SHIPPED blend
(A0, ``rank_blend.blend_scores``) against the quality-first key (A2,
``rank_blend_quality.quality_first_key``, per topic floor) on two label-free metrics:

* **contamination@k** — fraction of the top-k that is quality-poor (grade-D ∨ flag ∨
  rigor≤2). A quality-first order should surface FEWER of these.
* **q-lift@k** — the top-k's unified quality (mean + median) minus A0's. The directive
  ("a high-quality paper a little bit off-topic beats a poor-quality one on-topic") made
  measurable without the topic-biased user labels.

``goal_sim`` is read from the STORED ``aux_context.goal_sims`` (written by the gate's aux
pass), so there is NO corpus-embed pass — this run is light and memory-safe (unlike the
labeled AUC arms in ``eval_slate_blend.main``). Read-only; writes nothing.

Split out of ``eval_slate_blend.py`` to keep that file under the 500-LOC cap and because
label-free replay is a distinct responsibility from the labeled-AUC harness. Imports the
pure metric kernel (``_contamination_at`` / ``_q_at`` / ``_mean`` / ``_median`` /
``_is_contaminated``) from its sibling — those stay unit-tested in ``tests/test_eval_blend.py``.
"""
from __future__ import annotations

import os as _os
import sqlite3
import sys
from collections import defaultdict

# tools/ is not a package (scripts run directly); import the pure kernel from the sibling.
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from eval_slate_blend import (  # type: ignore[import-not-found]  # noqa: E402
    _contamination_at,
    _is_contaminated,
    _mean,
    _median,
    _q_at,
)


def _join_quality(row: dict, reviews: dict) -> dict:
    """Deep-review quality for a feed row via ``materialized_zotero_key`` (post-materialize)
    OR ``stable_feed_key`` (in-place Today review) — the two-key bridge the live slate uses
    (``daily_select._candidate.attach_quality_from_reviews``). Empty ``{}`` when unreviewed."""
    mkey = (row.get("materialized_zotero_key") or "").strip()
    skey = (row.get("stable_feed_key") or "").strip()
    entry = (reviews.get(mkey) if mkey else None) or (reviews.get(skey) if skey else None)
    return (entry or {}).get("quality") or {}


def _load_cohorts(settings_, min_cohort: int) -> list[list[dict]]:
    """Read every scored feed row, extract its parallel-list features from the STORED payload
    (no embed), and group by ``date(created_at)``. Keeps cohorts with >= ``min_cohort`` rows."""
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.model.rank_blend_quality import unified_quality
    from zotero_summarizer.services.triage.daily_select._candidate import (
        parse_payload, row_citation_percentile, row_goal_sim, row_triage_dim,
    )

    reviews = deep_review._read_all()
    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    raw = conn.execute(
        "SELECT date(created_at) AS day, composite_score, shap_contribs_json, "
        "materialized_zotero_key, stable_feed_key FROM processed_feed_items "
        "WHERE composite_score IS NOT NULL AND created_at IS NOT NULL"
    ).fetchall()
    conn.close()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        row = dict(r)
        payload = parse_payload(row)
        qual = _join_quality(row, reviews)
        grade, band = qual.get("grade"), qual.get("quality_band")
        rigor = row_triage_dim(payload, "methodological_rigor")
        by_day[row["day"]].append({
            "composite": float(row["composite_score"]),
            "goal_sim": row_goal_sim(payload),
            "citation_pct": row_citation_percentile(payload),
            "goal_alignment": row_triage_dim(payload, "goal_alignment"),
            "unified_q": unified_quality(grade, band, rigor=rigor,
                                         evidence=row_triage_dim(payload, "evidence_strength")),
            "is_bad": _is_contaminated(grade, band, rigor),
        })
    return [c for c in by_day.values() if len(c) >= min_cohort]


def run_replay(settings_, config, *, floors: tuple[float, ...], k: int = 10, min_cohort: int = 5) -> None:
    """Print the A0-vs-A2 contamination@k / q-lift@k frontier over daily cohorts, then the
    label-free half of the decision rule (contam drop >= 50% rel AND median q-lift >= +0.10)."""
    from zotero_summarizer.services.model.rank_blend import blend_scores
    from zotero_summarizer.services.model.rank_blend_quality import quality_first_key

    cohorts = _load_cohorts(settings_, min_cohort)
    if not cohorts:
        raise SystemExit(f"no daily cohort with >= {min_cohort} scored rows — cannot replay")
    n_rows = sum(len(c) for c in cohorts)
    n_signal = sum(1 for c in cohorts for row in c if row["is_bad"] or row["unified_q"] != 0.5)
    print(f"=== P2 label-free replay: {len(cohorts)} daily cohorts, {n_rows} scored rows "
          f"(>= {min_cohort}/day, k={k}); {n_signal} rows carry a quality/rigor signal ===")

    def _arm(keys_fn) -> tuple[float, float, float]:
        """Mean-over-cohorts (contamination@k, mean-q@k, median-q@k) for one ordering."""
        contam, meanq, medq = [], [], []
        for c in cohorts:
            keys = keys_fn(c)
            bad = [row["is_bad"] for row in c]
            q = [row["unified_q"] for row in c]
            contam.append(_contamination_at(keys, bad, k))
            meanq.append(_q_at(keys, q, k, agg=_mean))
            medq.append(_q_at(keys, q, k, agg=_median))
        return _mean(contam), _mean(meanq), _mean(medq)

    base_contam, base_meanq, base_medq = _arm(
        lambda c: blend_scores([r["composite"] for r in c], [r["goal_sim"] for r in c],
                               [r["citation_pct"] for r in c]))
    print(f"\n{'arm':<16}{'contam@k':>10}{'Δrel':>8}{'mean-q':>9}{'med-q':>8}{'q-lift(med)':>13}")
    print(f"{'A0 blend':<16}{base_contam:>10.3f}{'—':>8}{base_meanq:>9.3f}{base_medq:>8.3f}{'—':>13}")
    verdicts: dict[float, tuple[float, float]] = {}
    for floor in floors:
        mc, mm, md = _arm(
            lambda c, _f=floor: quality_first_key(
                [r["unified_q"] for r in c], [r["goal_sim"] for r in c],
                [r["goal_alignment"] for r in c], gate_floor=_f))
        drop = (base_contam - mc) / base_contam if base_contam > 0 else 0.0
        verdicts[floor] = (drop, md - base_medq)
        print(f"{f'A2 f={floor:.1f}':<16}{mc:>10.3f}{drop:>7.0%}{mm:>9.3f}{md:>8.3f}{md - base_medq:>+13.3f}")

    print("\ndecision rule (label-free half): contamination@k drop >= 50% rel AND median "
          "q-lift@k >= +0.10 (the AUC guardrail needs the heavy labeled run in eval_slate_blend).")
    for floor, (drop, qlift) in verdicts.items():
        ok = "PASS" if (drop >= 0.50 and qlift >= 0.10) else "fail"
        print(f"  f={floor:.1f}: contam_drop={drop:.0%} median_q_lift={qlift:+.3f} -> {ok}")
    print("NOTE: contamination@k shares the grade/rigor signal the A2 key ranks by, so a large "
          "A2 drop is partly BY CONSTRUCTION — the load-bearing number is A0's own contamination "
          f"({base_contam:.3f}): how many poor-quality papers the SHIPPED blend puts in the top-{k}.")
