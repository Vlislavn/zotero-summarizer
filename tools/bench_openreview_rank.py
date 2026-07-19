"""Offline 3-arm eval: does an OpenReview peer-review signal put TOP PAPERS ON TOP
without hurting relevance? (Phase 2a gate — decides the ranker arm before any
production rank.py change.)

Three ranking arms over the SAME federated pool (real sources + OpenReview), each a
drop-in sort key:

  A no-boost        the shipped contract, verbatim (rank.constrained_key)
  B within-bucket   + a bounded tier lift on leg 2 (reorders only WITHIN a bucket)
  C capped-promote  an accepted Oral/Spotlight near the top relevance is promoted up
                    to +2/+1 buckets, gated (query_score >= q_top - 0.10) and capped
                    (never above the top bucket) — codex's "top papers on top" fix

Two phases, split so the expensive part runs once:

  --fetch   HEAVY (network + local reranker): federate every query, attach the
            OpenReview peer-review signal, score query relevance, freeze the pool to
            JSON. Run user-coordinated on a memory-constrained box (loads the
            cross-encoder) — never unsupervised while a local LLM is resident.
  (default) OFFLINE: load the frozen pool, run all three arms, print the metric table
            + a promoted/displaced diff sheet for the domain expert to judge. No
            model, no network.
  --judge   OPTIONAL: a blind LLM relevance oracle (venue/tier/source hidden) for an
            independent precision@10 guardrail. Hits the configured LLM stage.

The go/no-go the user reads: (1) clinical/uncovered queries — A==B==C exactly (no
OpenReview coverage → zero change, an invariant); (2) ML/covered queries — does C
lift more Orals/Spotlights into the top-10 (benefit) while the top-10 mean
query_score stays within a small margin of A (relevance guardrail)? The
promoted/displaced sheet is the human spot-check.

Credentials: the OpenReview leaf reads OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD from
the environment. Run --fetch with them set, e.g.:

    uv run --env-file /path/to/.env python tools/bench_openreview_rank.py --fetch
    uv run python tools/bench_openreview_rank.py           # offline eval of the frozen pool
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zotero_summarizer.integrations.openalex import OpenAlexClient
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
from zotero_summarizer.integrations.openreview import (
    OpenReviewClient,
    OpenReviewHit,
    search_openreview,
)
from zotero_summarizer.services.search._models import Candidate, QueryPlan
from zotero_summarizer.services.search.rank import (
    GOAL_TIEBREAK_WEIGHT,
    _quality_evidence,
    constrained_key,
    score_query_relevance,
)

EPSILON = 0.05          # matches rank.DEFAULT_EPSILON (the eval seed can tune it)
GATE = 0.10             # relative relevance gate for cross-bucket promotion (arm C)
TIER_PROMOTION = {"oral": 2, "spotlight": 1}                       # poster/workshop → 0
TIER_LIFT = {"oral": 0.30, "spotlight": 0.20, "poster": 0.10, "workshop": 0.05}

# Configured venue set (the user's answer: Core ML + LLM/NLP + Medical ML + workshops)
# and year floor. In Phase 2b these move to models.config.OpenReviewConfig.
VENUE_HOSTS = ("ICLR.cc", "NeurIPS.cc", "ICML.cc", "COLM.org", "aclweb.org",
               "EMNLP", "MIDL.io", "ML4H", "CHIL", "TMLR")
YEAR_MIN = 2024

# 20 queries tagged by expected OpenReview coverage — the covered/uncovered split is
# the whole point (coverage bias is the biggest risk codex flagged). ``q`` is the full
# topic (drives federation + the cross-encoder query_score); ``or_q`` is a TIGHT keyword
# query for OpenReview only — its /notes/search buries real conference papers under
# DBLP/CoRR preprint mirrors when handed a long descriptive query (measured 2026-07-15),
# so the channel gets the tight variant, mirroring the lexical-source recall fix.
QUERIES: list[dict[str, str]] = [
    {"q": "retrieval augmented generation for large language models", "or_q": "retrieval augmented generation", "cov": "ml"},
    {"q": "llm agents that use tools and plan", "or_q": "language model agents", "cov": "ml"},
    {"q": "mixture of experts efficient training", "or_q": "mixture of experts", "cov": "ml"},
    {"q": "reinforcement learning from human feedback alignment", "or_q": "reinforcement learning from human feedback", "cov": "ml"},
    {"q": "diffusion models for image generation", "or_q": "diffusion models", "cov": "ml"},
    {"q": "chain of thought reasoning in language models", "or_q": "chain of thought", "cov": "ml"},
    {"q": "parameter efficient fine tuning low rank adaptation", "or_q": "low rank adaptation", "cov": "ml"},
    {"q": "long context transformers attention", "or_q": "long context transformers", "cov": "ml"},
    {"q": "graph neural networks representation learning", "or_q": "graph neural network", "cov": "ml"},
    {"q": "self supervised contrastive representation learning", "or_q": "contrastive learning", "cov": "ml"},
    {"q": "in context learning emergent abilities", "or_q": "in context learning", "cov": "ml"},
    {"q": "knowledge distillation model compression", "or_q": "knowledge distillation", "cov": "ml"},
    {"q": "multimodal vision language models", "or_q": "vision language model", "cov": "partial"},
    {"q": "federated learning privacy", "or_q": "federated learning", "cov": "partial"},
    {"q": "uncertainty quantification calibration deep learning", "or_q": "uncertainty quantification", "cov": "partial"},
    {"q": "medical image segmentation deep learning", "or_q": "medical image segmentation", "cov": "clinical"},
    {"q": "clinical large language model electronic health records", "or_q": "clinical language model", "cov": "clinical"},
    {"q": "cancer histopathology deep learning diagnosis", "or_q": "histopathology deep learning", "cov": "clinical"},
    {"q": "drug drug interaction prediction", "or_q": "drug interaction prediction", "cov": "clinical"},
    {"q": "survival analysis prognosis machine learning oncology", "or_q": "survival analysis deep learning", "cov": "clinical"},
]

_POOL_PATH = Path("data/bench/openreview_pool.json")


# --------------------------------------------------------------- ranking arms

@dataclass
class ArmCtx:
    """Query-level state the keys close over (arm C needs the pool max)."""

    q_top: float
    top_bucket: float


def _exact(cand: Candidate) -> float:
    goal = cand.goal_sim if cand.goal_sim is not None else 0.0
    qs = cand.query_score if cand.query_score is not None else 0.0
    return qs + GOAL_TIEBREAK_WEIGHT * goal


def key_no_boost(cand: Candidate, pr: dict | None, ctx: ArmCtx) -> tuple:
    return constrained_key(cand, epsilon=EPSILON)


def key_within_bucket(cand: Candidate, pr: dict | None, ctx: ArmCtx) -> tuple:
    qs = cand.query_score if cand.query_score is not None else 0.0
    if cand.is_retracted:
        return (float("-inf"), 0.0, qs)
    bucket = qs // EPSILON
    lift = TIER_LIFT.get((pr or {}).get("tier", ""), 0.0) if pr else 0.0
    return (bucket, _quality_evidence(cand) + lift, _exact(cand))


def key_capped_promotion(cand: Candidate, pr: dict | None, ctx: ArmCtx) -> tuple:
    # C = arm B (within-bucket tier lift) + a gated, capped cross-bucket promotion.
    # The promotion co-locates a near-top Oral INTO the top relevance bucket; the
    # leg-2 lift then wins the within-bucket tie against a more-relevant no-signal
    # paper. Promotion alone only reaches the bucket floor (leg-3 exact still favors
    # the higher query_score), so both legs are needed to actually put it on top.
    qs = cand.query_score if cand.query_score is not None else 0.0
    if cand.is_retracted:
        return (float("-inf"), 0.0, qs)
    raw_bucket = qs // EPSILON
    tier = (pr or {}).get("tier", "") if pr else ""
    promotion = TIER_PROMOTION.get(tier, 0)
    gated = promotion and qs >= ctx.q_top - GATE
    effective = min(raw_bucket + promotion, ctx.top_bucket) if gated else raw_bucket
    return (effective, _quality_evidence(cand) + TIER_LIFT.get(tier, 0.0), _exact(cand))


ARMS = {"A_no_boost": key_no_boost, "B_within_bucket": key_within_bucket,
        "C_capped_promote": key_capped_promotion}


def rank_by(arm, cands: list[Candidate], pr_by_id: dict[str, dict]) -> list[Candidate]:
    # q_top defines the promotion gate + bucket cap; retracted papers are hard-sunk,
    # so a retracted paper's high relevance must NOT raise the gate and suppress
    # legitimate promotions.
    live = [c for c in cands if c.query_score is not None and not c.is_retracted]
    q_top = max((c.query_score for c in live), default=0.0)
    ctx = ArmCtx(q_top=q_top, top_bucket=q_top // EPSILON)
    return sorted(cands, key=lambda c: arm(c, pr_by_id.get(c.candidate_id), ctx), reverse=True)


# ------------------------------------------------------------------- fetching

def _plan_for(query: str) -> QueryPlan:
    """A deterministic per-source plan (raw query into each lexical + semantic
    channel). The bench measures RANKING arms, not plan recall, so it skips the
    intent LLM — federation still runs the real sources + reranker."""
    return QueryPlan(
        openalex_lexical=query, openalex_semantic=query, europepmc=query,
        arxiv=query, crossref=query, semantic_scholar=query,
    )


def _cand_from_or_hit(h: OpenReviewHit, openalex: OpenAlexClient) -> Candidate:
    """An OpenReview paper as a Candidate, its id recovered via OpenAlex title
    resolution (fuzzy-guarded) so it dedups against the same paper from another
    source. OpenAlex ids come back as URLs; normalize to the short W-form federated
    candidates use."""
    openalex_id = ""
    work = openalex.fetch_work_by_title(h.title, year=h.year)
    if work is not None and work.openalex_id:
        openalex_id = work.openalex_id.rsplit("/", 1)[-1]
    return Candidate(
        title=h.title, abstract=h.abstract, authors=h.authors, year=h.year,
        venue=h.venue, url=h.url, openalex_id=openalex_id, is_open_access=True,
    )


def _attach_openreview(
    pool: list[Candidate], hits: list[OpenReviewHit], openalex: OpenAlexClient
) -> tuple[list[Candidate], dict[str, dict]]:
    """Merge OpenReview evidence into the pool by identifier (never title): if the
    paper is already present via another source, attach its peer_review to that
    candidate; else add it as a new candidate. Returns (augmented pool, peer_review
    map keyed by candidate_id)."""
    by_token: dict[str, Candidate] = {}
    for c in pool:
        for tok in c.identifier_keys():
            by_token.setdefault(tok, c)
    pr_by_id: dict[str, dict] = {}
    for h in hits:
        cand = _cand_from_or_hit(h, openalex)
        match = next((by_token[t] for t in cand.identifier_keys() if t in by_token), None)
        target = match or cand
        if match is None:
            pool.append(cand)
            for tok in cand.identifier_keys():
                by_token.setdefault(tok, cand)
        pr_by_id[target.candidate_id] = {
            "tier": h.tier, "venue": h.venue, "venueid": h.venueid,
            "mean_rating": h.mean_rating, "n_reviews": h.n_reviews,
        }
    return pool, pr_by_id


def fetch(out: Path) -> int:
    """HEAVY: federate + attach OpenReview + score every query, freeze to JSON."""
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.search.federate import federate
    from zotero_summarizer.settings import Settings

    settings = Settings.load()
    config = read_config(settings.config_path)
    cache = OpenAlexCache(settings.data_dir / "openreview_bench" / "openalex_cache.db")
    openalex = OpenAlexClient(cache, mailto=config.prestige.user_agent_email or None)
    or_client = OpenReviewClient(
        venue_hosts=VENUE_HOSTS, year_min=YEAR_MIN,
        cache=OpenAlexCache(settings.data_dir / "openreview_bench" / "or_cache.db"),
    )
    if not or_client.enabled:
        print("OpenReview disabled (set OPENREVIEW_USERNAME/PASSWORD). Aborting fetch.")
        return 2

    frozen: list[dict] = []
    for i, item in enumerate(QUERIES, 1):
        q = item["q"]
        pool = federate(_plan_for(q), openalex_client=openalex, quota=12)
        or_hits = search_openreview(or_client, item.get("or_q", q), limit=60)
        pool, pr_by_id = _attach_openreview(pool, or_hits, openalex)
        score_query_relevance(q, pool, reranker_model=config.corpus.reranker_model)
        frozen.append({
            "query": q, "cov": item["cov"],
            "candidates": [c.to_dict() for c in pool],
            "peer_review": pr_by_id,
        })
        print(f"[{i:2d}/{len(QUERIES)}] {item['cov']:8s} {q[:44]:44s} "
              f"pool={len(pool):3d} openreview={len(or_hits):2d}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(frozen, indent=1))
    print(f"\nfroze {len(frozen)} queries → {out}")
    return 0


# ------------------------------------------------------------------- eval

@dataclass
class QueryEval:
    query: str
    cov: str
    n_openreview: int
    top10: dict[str, list[Candidate]] = field(default_factory=dict)
    pr_by_id: dict[str, dict] = field(default_factory=dict)


def _load(path: Path) -> list[dict]:
    rows = json.loads(path.read_text())
    for row in rows:
        row["candidates"] = [Candidate.from_dict(c) for c in row["candidates"]]
    return rows


def _top_papers_in(cands: list[Candidate], pr: dict[str, dict], k: int = 10) -> int:
    """How many Oral/Spotlight (main-conf) papers sit in the top-k."""
    top = {"oral", "spotlight"}
    return sum(1 for c in cands[:k] if pr.get(c.candidate_id, {}).get("tier") in top)


def _mean_qs(cands: list[Candidate], k: int = 10) -> float:
    vals = [c.query_score or 0.0 for c in cands[:k]]
    return sum(vals) / len(vals) if vals else 0.0


def evaluate(rows: list[dict]) -> list[QueryEval]:
    out: list[QueryEval] = []
    for row in rows:
        pr = row["peer_review"]
        qe = QueryEval(query=row["query"], cov=row["cov"], n_openreview=len(pr), pr_by_id=pr)
        for name, arm in ARMS.items():
            qe.top10[name] = rank_by(arm, list(row["candidates"]), pr)
        out.append(qe)
    return out


def _title(c: Candidate) -> str:
    return (c.title or c.candidate_id)[:58]


def report(evals: list[QueryEval]) -> None:
    print("\n=== per-query: Oral/Spotlight in top-10, and top-10 mean query_score ===")
    print(f"{'cov':8s} {'#OR':>3} | {'A_top':>5} {'B_top':>5} {'C_top':>5} | "
          f"{'A_qs':>5} {'C_qs':>5}  query")
    agg = {"ml": [0, 0, 0], "partial": [0, 0, 0], "clinical": [0, 0, 0]}
    qs_cost: list[float] = []
    for qe in evals:
        a, b, c = (qe.top10[n] for n in ARMS)
        ta = _top_papers_in(a, qe.pr_by_id); tb = _top_papers_in(b, qe.pr_by_id)
        tc = _top_papers_in(c, qe.pr_by_id)
        qa, qc = _mean_qs(a), _mean_qs(c)
        qs_cost.append(qa - qc)
        agg.setdefault(qe.cov, [0, 0, 0])
        agg[qe.cov][0] += ta; agg[qe.cov][1] += tb; agg[qe.cov][2] += tc
        print(f"{qe.cov:8s} {qe.n_openreview:3d} | {ta:5d} {tb:5d} {tc:5d} | "
              f"{qa:5.2f} {qc:5.2f}  {qe.query[:40]}")
    print("\n=== aggregate Oral/Spotlight in top-10 (sum over queries) ===")
    for cov, (a, b, c) in agg.items():
        print(f"  {cov:8s}  A={a:3d}  B={b:3d}  C={c:3d}")
    worst = max(qs_cost) if qs_cost else 0.0
    print(f"\nrelevance guardrail: max top-10 mean-query_score drop A→C = {worst:.3f} "
          f"(smaller is safer)")

    print("\n=== promoted / displaced under C (vs A) — the human spot-check sheet ===")
    for qe in evals:
        a10 = {c.candidate_id for c in qe.top10["A_no_boost"][:10]}
        c10 = qe.top10["C_capped_promote"][:10]
        promoted = [c for c in c10 if c.candidate_id not in a10]
        if not promoted and qe.cov == "clinical":
            continue  # expected no-op; shown in the invariant check below
        if not promoted:
            continue
        print(f"\n  [{qe.cov}] {qe.query}")
        for c in promoted:
            pr = qe.pr_by_id.get(c.candidate_id, {})
            rating = (f"{pr['mean_rating']:.1f}/{pr['n_reviews']}rev"
                      if pr.get("mean_rating") is not None else "no-rev")
            print(f"    ↑ {pr.get('tier','?'):9s} qs={c.query_score or 0:.2f} "
                  f"{rating:11s} {_title(c)}")

    # Invariant: a query with ZERO OpenReview signal must be byte-identical across all
    # arms (no peer_review → nothing to lift/promote). This is the real correctness
    # guarantee — NOT "clinical", since a clinical query can still catch a stray hit.
    print("\n=== invariant: zero-signal queries unchanged across arms ===")
    bad = checked = 0
    for qe in evals:
        if qe.n_openreview != 0:
            continue
        checked += 1
        ids = {n: [c.candidate_id for c in qe.top10[n]] for n in ARMS}
        if not (ids["A_no_boost"] == ids["B_within_bucket"] == ids["C_capped_promote"]):
            bad += 1
            print(f"  VIOLATION: {qe.query} — arms diverged with no OpenReview signal")
    print(f"  {f'OK — all {checked} zero-signal queries identical across arms' if not bad else f'{bad} VIOLATION(S)'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true", help="HEAVY: federate+score, freeze the pool")
    ap.add_argument("--pool", type=Path, default=_POOL_PATH, help="frozen pool JSON path")
    ap.add_argument("--env-file", type=Path, help="dotenv file with OPENREVIEW_* creds "
                    "(python-dotenv parser; use instead of `uv run --env-file`, which mis-parses "
                    "unquoted values)")
    ap.add_argument("--selfcheck", action="store_true", help="run the offline arm-math self-check and exit")
    args = ap.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    if args.selfcheck:
        demo()
        return 0
    if args.fetch:
        return fetch(args.pool)
    if not args.pool.exists():
        print(f"no frozen pool at {args.pool}. Run --fetch first (user-coordinated, loads the reranker).")
        return 2
    report(evaluate(_load(args.pool)))
    return 0


# --------------------------------------------------------------- self-check

def demo() -> None:
    """Offline arm-math check on a synthetic pool — no model, no network. Proves the
    codex mechanism: a near-top Oral crosses buckets (capped), an off-topic Oral is
    gated out, a retracted paper sinks, and within-bucket never crosses."""
    def c(cid, qs, retracted=False):
        return Candidate(title=cid, doi=cid, query_score=qs, is_retracted=retracted)

    poster = c("poster", 0.91)     # bucket 18, more relevant, no signal
    oral = c("oral", 0.83)         # bucket 16, an accepted Oral just below top
    offtopic = c("offtopic", 0.50) # bucket 10, an Oral but far from the query
    weak = c("weak", 0.60)         # bucket 12, no signal
    retracted = c("retracted", 0.95, retracted=True)
    pool = [poster, oral, offtopic, weak, retracted]
    pr = {
        "oral": {"tier": "oral"}, "offtopic": {"tier": "oral"},
    }

    a = [x.candidate_id for x in rank_by(key_no_boost, list(pool), pr)]
    b = [x.candidate_id for x in rank_by(key_within_bucket, list(pool), pr)]
    cc = [x.candidate_id for x in rank_by(key_capped_promotion, list(pool), pr)]

    # A: pure relevance order (retracted sinks last).
    assert a == ["poster", "oral", "weak", "offtopic", "retracted"], a
    # B: within-bucket lift can't move the Oral across buckets → same top as A.
    assert b[0] == "poster" and b.index("oral") == 1, b
    # C: the near-top Oral (0.83, gate 0.91-0.10=0.81) promotes +2 → bucket 18, ties
    # the poster's bucket, and its quality/exact break the tie; the off-topic Oral
    # (0.50 < 0.81) is gated OUT and stays below 'weak'; retracted still sinks.
    assert cc[0] == "oral" and cc[1] == "poster", cc
    assert cc.index("offtopic") > cc.index("weak"), cc
    assert cc[-1] == "retracted", cc
    # Promotion is capped: 'oral' reaches the top bucket, never above it.
    print("selfcheck OK  A:", a, "\n              C:", cc)


if __name__ == "__main__":
    raise SystemExit(main())
