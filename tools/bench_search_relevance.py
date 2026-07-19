"""Phase 0 of the relevance-signal workstream: HONEST per-query relevance metrics for the
Targeted Search reranker, using the cached dual-judge labels (no LLM, no network, no model).

Codex (gpt-5.6-sol) demolished the pooled Spearman(query_score, rel)=0.443 as the WRONG
statistic — pooled over 341 pairs it mixes each query's separate score scale. This computes
the right ones, MACRO per-query and PER-JUDGE (the two judges are correlated measurements,
never one averaged truth):

  - score-only NDCG@10 / P@10          (deployed key == score-only here: quality is 0/912)
  - pairwise concordance (tie-aware)   (frac of distinct-rel pairs ordered right by query_score)
  - top-10 harmful inversions          (rel_i < rel_j yet i ranked above j, both in top-10)
  - buried-relevant                    (rel>=2 outside top-10 while a rel<=1 sits inside)
  - min query_score among rel==3       (does the reranker score clearly-relevant papers low?)
  - 95% query-bootstrap CI on NDCG@10 + concordance

The go/no-go (codex): if the deployed ranking doesn't harm top-10 and score-only NDCG@10 is
already high, the "weak reranker / mixed buckets" story is a statistical artifact → ship
nothing on the mechanism. What headroom EXISTS shows up as buried-relevant + mid-rank
concordance, which only a better model / input can fix (L2), not the bucket width (L1).

    uv run python tools/bench_search_relevance.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "tools")
from bench_openreview_rank import _load  # noqa: E402
from bench_openreview_judge import ndcg_at_k, precision_at_k  # noqa: E402  (reuse the graded metrics)

POOL = Path("data/bench/openreview_pool.json")
JUDGES = ("GPT-OSS-120B", "gemma-4-31B-it")
REL = 2  # rel>=REL counts as "relevant"


def _labels(judge: str) -> dict:
    safe = judge.replace("/", "_").replace(".", "_")
    p = Path(f"data/bench/openreview_labels_{safe}.json")
    return json.loads(p.read_text())["by_query"]


def score_only(cands):
    """The base-ranker order: query_score desc, retracted sunk, stable id tiebreak."""
    return sorted(
        cands,
        key=lambda c: (-1e9 if c.is_retracted else (c.query_score or 0.0), c.candidate_id),
        reverse=True,
    )


def concordance(cands, lab: dict[str, int]) -> float | None:
    """Tie-aware pairwise concordance (AUC / Somers'-D flavour): over all candidate pairs
    with DISTINCT judged relevance, the fraction that query_score orders correctly; a
    query_score tie counts 0.5. 1.0=perfect, 0.5=random, <0.5=inverted. None if <1 pair."""
    js = [(c.query_score or 0.0, lab.get(c.candidate_id)) for c in cands]
    js = [(q, r) for q, r in js if r is not None]
    num = den = 0.0
    for i in range(len(js)):
        for j in range(i + 1, len(js)):
            qi, ri = js[i]
            qj, rj = js[j]
            if ri == rj:
                continue
            den += 1
            hi_rel_q = qi if ri > rj else qj
            lo_rel_q = qj if ri > rj else qi
            num += 1.0 if hi_rel_q > lo_rel_q else (0.5 if hi_rel_q == lo_rel_q else 0.0)
    return num / den if den else None


def top10_inversions(ranked, lab) -> int:
    top = [c for c in ranked[:10]]
    inv = 0
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            ri, rj = lab.get(top[i].candidate_id, 0), lab.get(top[j].candidate_id, 0)
            if ri < rj:  # a less-relevant paper ranked above a more-relevant one
                inv += 1
    return inv


def buried_relevant(ranked, lab) -> int:
    """# of rel>=REL papers pushed OUT of top-10 while a rel<REL paper sits inside it."""
    top_ids = {c.candidate_id for c in ranked[:10]}
    has_weak_in_top = any(lab.get(c.candidate_id, 0) < REL for c in ranked[:10])
    if not has_weak_in_top:
        return 0
    return sum(1 for c in ranked[10:] if lab.get(c.candidate_id, 0) >= REL and c.candidate_id not in top_ids)


def min_qs_of_rel3(cands, lab) -> float | None:
    v = [(c.query_score or 0.0) for c in cands if lab.get(c.candidate_id) == 3]
    return min(v) if v else None


def _boot_ci(vals: list[float], *, lo=2.5, hi=97.5, n=2000) -> tuple[float, float]:
    """Query-clustered bootstrap: resample the per-query values with replacement.
    Deterministic LCG (no Math.random dependency, reproducible)."""
    if len(vals) < 2:
        return (float("nan"), float("nan"))
    seed = 0x2545F4914F6CDD1D
    means = []
    m = len(vals)
    for _ in range(n):
        s = 0.0
        for _ in range(m):
            seed = (6364136223846793005 * seed + 1442695040888963407) & ((1 << 64) - 1)
            s += vals[(seed >> 33) % m]
        means.append(s / m)
    means.sort()
    return (means[int(lo / 100 * n)], means[int(hi / 100 * n)])


def report_for(rows, judge: str) -> None:
    lab_by_q = _labels(judge)
    per_cov = {}
    ndcg_all, conc_all, inv_all, buried_all = [], [], [], []
    low_rel3 = []
    for r in rows:
        q, cov = r["query"], r["cov"]
        lab = lab_by_q.get(q)
        if not lab:
            continue
        ranked = score_only(r["candidates"])
        nd = ndcg_at_k(ranked, lab)
        pr = precision_at_k(ranked, lab)
        cc = concordance(r["candidates"], lab)
        inv = top10_inversions(ranked, lab)
        bur = buried_relevant(ranked, lab)
        m3 = min_qs_of_rel3(r["candidates"], lab)
        per_cov.setdefault(cov, []).append((nd, pr, cc, inv, bur))
        ndcg_all.append(nd)
        if cc is not None:
            conc_all.append(cc)
        inv_all.append(inv)
        buried_all.append(bur)
        if m3 is not None and m3 < 0.5:
            low_rel3.append((m3, q))

    print(f"\n{'='*72}\njudge = {judge}  (score-only order; deployed key == this, quality 0/912)\n{'='*72}")
    print(f"{'cov':>9} | {'n':>2} | {'NDCG@10':>13} | {'P@10':>6} | {'concord':>7} | {'inv10':>5} | {'buried':>6}")
    for cov in ("ml", "partial", "clinical"):
        v = per_cov.get(cov)
        if not v:
            continue
        nds = [x[0] for x in v]
        prs = [x[1] for x in v]
        ccs = [x[2] for x in v if x[2] is not None]
        invs = [x[3] for x in v]
        burs = [x[4] for x in v]
        print(f"{cov:>9} | {len(v):>2} | {statistics.mean(nds):.3f}/{statistics.median(nds):.3f} m/med | "
              f"{statistics.mean(prs):.3f} | {statistics.mean(ccs):.3f} | {statistics.mean(invs):5.1f} | {statistics.mean(burs):6.2f}")

    lo, hi = _boot_ci(ndcg_all)
    clo, chi = _boot_ci(conc_all)
    print(f"\n  ALL (n={len(ndcg_all)}): NDCG@10 mean={statistics.mean(ndcg_all):.3f}  95%CI[{lo:.3f},{hi:.3f}]")
    print(f"                concordance mean={statistics.mean(conc_all):.3f}  95%CI[{clo:.3f},{chi:.3f}]")
    print(f"                top-10 harmful inversions/query = {statistics.mean(inv_all):.2f}")
    print(f"                buried relevant/query           = {statistics.mean(buried_all):.2f}")
    if low_rel3:
        print(f"  rel=3 papers scored qs<0.5 (reranker under-scores clearly-relevant):")
        for m3, q in sorted(low_rel3):
            print(f"      min_qs={m3:.3f}  {q[:52]}")


def main() -> int:
    if not POOL.exists():
        print(f"no pool at {POOL}")
        return 2
    rows = _load(POOL)
    for judge in JUDGES:
        report_for(rows, judge)
    print("\n(quality/goal_sim are 0/912 on this pool, so deployed ε=0.05 == score-only; "
          "L1 bucketing is inert here. Headroom, if any, is buried-relevant + concordance → L2/input.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
