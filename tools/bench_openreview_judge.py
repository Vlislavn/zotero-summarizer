"""REAL eval: an independent BLIND relevance judge → non-circular NDCG@10 / precision@10.

The offline arm eval (``bench_openreview_rank.py``) used the cross-encoder ``query_score``
as BOTH the ranking key AND the relevance guardrail — circular, so its "0.007 relevance
cost" was near-tautological. This adds the plan's missing instrument: a pinned LLM judge
that scores topical relevance from ONLY the topic + title + abstract, never seeing
venue / tier / source / query_score / rank (the mira "sees-both-lists" grader
anti-pattern). Its labels feed real NDCG@10 / precision@10 that the arms compete on.

  Ref: are/pinned-judge-model (fully-qualified pinned id, recorded per run)
       are/hard-before-soft-judge (deterministic first; here a cheap cache before any call)
       mira/grader-anti-patterns (judge must NOT see the signal it is validating)

One call per query over the union of each arm's top-15 (batched; graded 0..3), cached to
``data/bench/openreview_relevance_labels.json`` so re-runs are free. Remote endpoint
(``CUSTOM_BASE_URL``) — no local memory cost.

    uv run --env-file <.env> python tools/bench_openreview_judge.py          # judge (cached) + report
    uv run python tools/bench_openreview_judge.py --report-only              # metrics from cache, no LLM
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "tools")
import bench_openreview_rank as B  # noqa: E402  (path insert first)
from bench_openreview_rank import (  # noqa: E402
    TIER_LIFT,
    _load,
    key_capped_promotion,
    key_no_boost,
    key_within_bucket,
    rank_by,
)

LABELS_PATH = Path("data/bench/openreview_relevance_labels.json")
POOL_PATH = Path("data/bench/openreview_pool.json")
REL_RELEVANT = 2          # judge label >= this counts as "relevant" for precision@10
UNION_K = 15              # judge the union of each arm's top-15 (covers every arm's top-10)
MAX_ABSTRACT_CHARS = 1000  # cap per-paper prompt size


# ---------------------------------------------------------------- RRF arm (SOTA baseline)

def rank_rrf(cands, pr, k=60, w=1.0):
    """Reciprocal Rank Fusion of a relevance list (query_score) with a sparse quality
    list (papers WITH a tier, by tier strength). Distribution-free — no epsilon, immune
    to the score saturation that made the bucket arms an epsilon artifact. Retracted sunk."""
    by_rel = sorted(cands, key=lambda c: (c.query_score or 0.0), reverse=True)
    rel = {c.candidate_id: i + 1 for i, c in enumerate(by_rel)}
    tiered = [c for c in cands if pr.get(c.candidate_id, {}).get("tier") in TIER_LIFT]
    tiered.sort(key=lambda c: TIER_LIFT[pr[c.candidate_id]["tier"]], reverse=True)
    qual = {c.candidate_id: i + 1 for i, c in enumerate(tiered)}  # ponytail: tie ranks arbitrary; fine for a baseline

    def score(c):
        if c.is_retracted:
            return -1.0
        s = 1.0 / (k + rel[c.candidate_id])
        if c.candidate_id in qual:
            s += w * 1.0 / (k + qual[c.candidate_id])
        return s
    return sorted(cands, key=score, reverse=True)


def _arms(cands, pr):
    """The four rankings under comparison. B/C use the shipped EPSILON=0.05."""
    return {
        "A_no_boost": rank_by(key_no_boost, list(cands), pr),
        "B_within": rank_by(key_within_bucket, list(cands), pr),
        "C_promote": rank_by(key_capped_promotion, list(cands), pr),
        "RRF": rank_rrf(list(cands), pr),
    }


# ---------------------------------------------------------------- blind judge

_JUDGE_PROMPT = """You are an impartial relevance assessor for a scientific-paper search system.
Given a user's search TOPIC and a numbered list of papers (title + abstract only), rate how \
relevant EACH paper is to the TOPIC on this scale:
  3 = highly relevant: the paper is directly about the topic (a core contribution on it)
  2 = relevant: clearly on-topic, substantially addresses the topic
  1 = marginally related: touches the topic only tangentially / in passing
  0 = off-topic: not about this topic

Judge ONLY topical relevance from the title and abstract. Do NOT consider venue, authors, \
recency, citations, or perceived quality — only whether the paper is about the TOPIC.

TOPIC: {query}

PAPERS:
{papers}

Return STRICT JSON only, no prose, no code fences:
{{"ratings": [{{"i": <paper number>, "rel": <0|1|2|3>}}, ...]}} with exactly one entry per paper."""


def _render_papers(cands) -> str:
    out = []
    for i, c in enumerate(cands, 1):
        abstract = (c.abstract or "").strip()[:MAX_ABSTRACT_CHARS] or "(no abstract)"
        out.append(f"[{i}] {c.title or '(untitled)'}\n{abstract}")
    return "\n\n".join(out)


def judge_query(judge_llm, query: str, cands) -> dict[str, int]:
    """One blind, batched call → {candidate_id: relevance 0..3}. Neutral (title-sorted)
    order so the arm ordering never leaks into the judge."""
    from zotero_summarizer.services.faithbench._judge import _judge_json

    ordered = sorted(cands, key=lambda c: (c.title or "").lower())
    prompt = _JUDGE_PROMPT.format(query=query, papers=_render_papers(ordered))
    payload = _judge_json(judge_llm, prompt)  # parses JSON, one reinforced retry inside
    by_i = {int(r["i"]): int(r["rel"]) for r in payload["ratings"]}
    labels: dict[str, int] = {}
    for i, c in enumerate(ordered, 1):
        if i not in by_i:
            raise ValueError(f"judge omitted paper {i} for query {query!r}")
        labels[c.candidate_id] = max(0, min(3, by_i[i]))
    return labels


def _build_judge(judge_model: str):
    from zotero_summarizer.models.providers import ProviderConfig, ProviderType
    from zotero_summarizer.services.faithbench._constants import (
        DEFAULT_JUDGE_API_KEY_ENV,
        DEFAULT_JUDGE_BASE_URL_ENV,
        JUDGE_MAX_TOKENS,
    )
    from zotero_summarizer.services.llm.factory import build_client_for_provider

    provider = ProviderConfig(
        name="judge", type=ProviderType.openai,
        base_url=os.environ[DEFAULT_JUDGE_BASE_URL_ENV],
        api_key_env=DEFAULT_JUDGE_API_KEY_ENV, extra_body=None,
        max_tokens=JUDGE_MAX_TOKENS, temperature=0.0,  # deterministic grader
    )
    return build_client_for_provider(provider, judge_model), judge_model


def _union_to_label(row) -> list:
    """Candidates that appear in ANY arm's top-UNION_K — the set that needs labels."""
    pr = row["peer_review"]
    seen: dict[str, Any] = {}
    for ranked in _arms(row["candidates"], pr).values():
        for c in ranked[:UNION_K]:
            seen.setdefault(c.candidate_id, c)
    return list(seen.values())


def _labels_path(judge_model: str) -> Path:
    safe = judge_model.replace("/", "_").replace(".", "_")
    return LABELS_PATH.with_name(f"openreview_labels_{safe}.json")


def label_all(rows, *, refresh: bool, judge_model: str) -> dict[str, Any]:
    """Judge every query's union (cached, incremental, per-judge-model). Returns the store."""
    path = _labels_path(judge_model)
    store: dict[str, Any] = {"judge_model": judge_model, "by_query": {}}
    if path.exists() and not refresh:
        store = json.loads(path.read_text())
        if store["judge_model"] != judge_model:
            raise ValueError(f"cache {path} was judged by {store['judge_model']}, not {judge_model}")
    judge_llm = None
    for row in rows:
        q = row["query"]
        need = _union_to_label(row)
        have = store["by_query"].get(q, {})
        if all(c.candidate_id in have for c in need):
            print(f"  [cached] {q[:50]}")
            continue
        if judge_llm is None:
            judge_llm, _ = _build_judge(judge_model)
            print(f"judge = {judge_model} @ {os.environ['CUSTOM_BASE_URL']}")
        labels = judge_query(judge_llm, q, need)  # judge the full union (stable order)
        store["by_query"][q] = labels
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, indent=1))  # checkpoint after each query
        rel = sum(1 for v in labels.values() if v >= REL_RELEVANT)
        print(f"  judged {len(labels):2d}  relevant(≥{REL_RELEVANT})={rel:2d}  {q[:50]}")
    return store


# ---------------------------------------------------------------- metrics

def ndcg_at_k(ranked, labels: dict[str, int], k: int = 10) -> float:
    def gain(rel):
        return (2 ** rel - 1)
    dcg = sum(gain(labels.get(c.candidate_id, 0)) / math.log2(i + 1)
              for i, c in enumerate(ranked[:k], 1))
    ideal = sorted(labels.values(), reverse=True)[:k]
    idcg = sum(gain(r) / math.log2(i + 1) for i, r in enumerate(ideal, 1))
    return dcg / idcg if idcg > 0 else 0.0


def precision_at_k(ranked, labels: dict[str, int], k: int = 10) -> float:
    top = ranked[:k]
    if not top:
        return 0.0
    return sum(1 for c in top if labels.get(c.candidate_id, 0) >= REL_RELEVANT) / len(top)


def report(rows, store) -> None:
    labels_by_q = store["by_query"]
    arms = ["A_no_boost", "B_within", "C_promote", "RRF"]
    # per-cov accumulators of per-query NDCG@10 / P@10
    ndcg = {a: {"ml": [], "partial": [], "clinical": []} for a in arms}
    prec = {a: {"ml": [], "partial": [], "clinical": []} for a in arms}
    covered = {a: [] for a in arms}  # NDCG only over queries WITH peer_review signal

    for row in rows:
        q, cov = row["query"], row["cov"]
        labels = labels_by_q.get(q)
        if labels is None:
            print(f"  (no labels for {q[:50]} — run without --report-only)")
            continue
        ranked = _arms(row["candidates"], row["peer_review"])
        for a in arms:
            n = ndcg_at_k(ranked[a], labels)
            ndcg[a].setdefault(cov, []).append(n)
            prec[a].setdefault(cov, []).append(precision_at_k(ranked[a], labels))
            if row["peer_review"]:
                covered[a].append(n)

    print(f"\n=== judge = {store['judge_model']} (blind: topic+title+abstract only) ===")
    print("\n=== NDCG@10 (independent relevance labels) — mean / median ===")
    print(f"{'arm':>12} | " + " | ".join(f"{c:>15}" for c in ("ml", "partial", "clinical")))
    for a in arms:
        cells = []
        for c in ("ml", "partial", "clinical"):
            v = ndcg[a][c]
            cells.append(f"{statistics.mean(v):.3f}/{statistics.median(v):.3f}" if v else "  —  ")
        print(f"{a:>12} | " + " | ".join(f"{x:>15}" for x in cells))

    print("\n=== precision@10 (frac of top-10 with relevance ≥ 2) — mean ===")
    print(f"{'arm':>12} | " + " | ".join(f"{c:>10}" for c in ("ml", "partial", "clinical")))
    for a in arms:
        cells = [f"{statistics.mean(prec[a][c]):.3f}" if prec[a][c] else "  — " for c in ("ml", "partial", "clinical")]
        print(f"{a:>12} | " + " | ".join(f"{x:>10}" for x in cells))

    print("\n=== NDCG@10 on COVERED queries only (has peer_review) — mean / median ===")
    for a in arms:
        v = covered[a]
        print(f"  {a:>12}  {statistics.mean(v):.3f} / {statistics.median(v):.3f}   (n={len(v)})")

    # The money question: are the papers C/RRF PROMOTE (vs A) actually relevant per the
    # independent judge, or is promotion trading relevance for prestige?
    print("\n=== promoted-into-top-10 vs A: judge relevance of each promoted paper ===")
    for row in rows:
        q = row["query"]
        labels = labels_by_q.get(q)
        if not labels or not row["peer_review"]:
            continue
        ranked = _arms(row["candidates"], row["peer_review"])
        a10 = {c.candidate_id for c in ranked["A_no_boost"][:10]}
        for arm in ("C_promote", "RRF"):
            promoted = [c for c in ranked[arm][:10] if c.candidate_id not in a10]
            for c in promoted:
                pr = row["peer_review"].get(c.candidate_id, {})
                rel = labels.get(c.candidate_id, 0)
                flag = "GOOD" if rel >= REL_RELEVANT else "COST"  # relevant vs relevance-traded
                print(f"  [{arm:9s}] rel={rel} {flag}  {pr.get('tier','?'):9s} "
                      f"qs={c.query_score or 0:.2f}  {(c.title or '')[:52]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", type=Path, default=POOL_PATH)
    ap.add_argument("--judge-model", default="GPT-OSS-120B",
                    help="pinned, recorded judge id (must be WARM on the endpoint)")
    ap.add_argument("--env-file", type=Path, help="dotenv with CUSTOM_BASE_URL/CUSTOM_API_KEY")
    ap.add_argument("--refresh", action="store_true", help="re-judge from scratch (ignore cache)")
    ap.add_argument("--report-only", action="store_true", help="metrics from cache, no LLM")
    args = ap.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    if not args.pool.exists():
        print(f"no frozen pool at {args.pool}; run bench_openreview_rank.py --fetch first")
        return 2
    rows = _load(args.pool)
    if args.report_only:
        path = _labels_path(args.judge_model)
        if not path.exists():
            print(f"no labels at {path}; run without --report-only to judge first")
            return 2
        store = json.loads(path.read_text())
    else:
        store = label_all(rows, refresh=args.refresh, judge_model=args.judge_model)
    report(rows, store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
