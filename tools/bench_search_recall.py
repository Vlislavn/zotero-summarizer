"""Recall@pool bench for the Targeted Search quoted-phrase fix (on-demand, network).

Measures the thing the fix is supposed to move: does a known-relevant paper land in
the CANDIDATE POOL (`federate()` output, BEFORE the reranker)? The keyword-bag query
`build_query_plan` used to emit buries specific/named papers under OpenAlex's
citation-weighted relevance; the fix adds a tight quoted-phrase variant per lexical
source. This runs the REAL pipeline (real intent parse + live arXiv/EuropePMC/OpenAlex)
and reports, per seed, whether the baseline (bag only) and the fix (tight+bag) surface
the expected id — and which query variant surfaced a fix-only win. One run gives the
before→after; no app restart needed (the baseline strips the variants in-process).

Not a unit test — it hits the network and the LLM. Run by hand:

  OPENAI_API_KEY=… uv run python tools/bench_search_recall.py            # 7 built-in seeds
  … uv run python tools/bench_search_recall.py --seeds my_seeds.json --quota 15

Seeds file: JSON list of {"topic": str, "doi": str?, "arxiv": str?}. Every id was
verified live against OpenAlex before shipping (never trust an LLM-recalled id).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi
from zotero_summarizer.integrations.openalex import OpenAlexClient
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.llm.factory import build_client_for_stage
from zotero_summarizer.services.search._models import Candidate, QueryPlan
from zotero_summarizer.services.search.federate import federate
from zotero_summarizer.services.search.intent import build_query_plan, parse_intent
from zotero_summarizer.settings import Settings

# All ids verified live via OpenAlex title.search on 2026-07-11 (see module docstring).
_DEFAULT_SEEDS: list[dict[str, str]] = [
    {"topic": "evaluation of LLM-based agents: benchmarks and survey",
     "doi": "10.18653/v1/2026.findings-acl.1330", "arxiv": "2503.16416"},  # the CAPA target
    {"topic": "benchmark evaluating LLMs as autonomous agents",
     "doi": "10.48550/arxiv.2308.03688", "arxiv": "2308.03688"},           # AgentBench
    {"topic": "survey of large language model based autonomous agents",
     "doi": "10.1007/s11704-024-40231-1"},                                 # LLM-agents survey
    {"topic": "retrieval augmented generation for large language models survey",
     "doi": "10.48550/arxiv.2312.10997", "arxiv": "2312.10997"},           # RAG survey
    {"topic": "language models that teach themselves to use tools",
     "doi": "10.48550/arxiv.2302.04761", "arxiv": "2302.04761"},           # Toolformer
    {"topic": "evaluating collaboration and competition of LLM agents",
     "doi": "10.18653/v1/2025.acl-long.421"},                              # MultiAgentBench
    {"topic": "the rise and potential of LLM based agents: a survey",
     "doi": "10.1007/s11432-024-4222-0"},                                  # Rise & Potential
]


def _build_openalex(settings: Settings) -> OpenAlexClient:
    """A network-enabled OpenAlex client with a bench-local cache (under data/)."""
    config = read_config(settings.config_path)
    cache = OpenAlexCache(settings.data_dir / "search_recall_bench" / "openalex_cache.db")
    return OpenAlexClient(cache, mailto=config.prestige.user_agent_email or None)


def _matches(pool: list[Candidate], doi: str, arxiv: str) -> Candidate | None:
    """First pooled candidate whose normalized DOI or arXiv id equals the expected id."""
    want_doi = normalize_doi(doi) if doi else ""
    want_arxiv = normalize_arxiv_id(arxiv) if arxiv else ""
    for cand in pool:
        if want_doi and cand.doi and cand.doi == want_doi:
            return cand
        if want_arxiv and cand.arxiv_id and cand.arxiv_id == want_arxiv:
            return cand
    return None


def _bag_only(plan: QueryPlan) -> QueryPlan:
    """The pre-fix plan: empty variant lists → federate falls back to the scalar bag."""
    return dataclasses.replace(
        plan, openalex_lexical_variants=[], europepmc_variants=[], arxiv_variants=[],
    )


def _surfacing_variant(cand: Candidate) -> str:
    """The tightest query_variant that surfaced this candidate (a quoted phrase means
    the tight pass found it — the whole point of the fix)."""
    variants = [p.query_variant for p in cand.provenance]
    quoted = [v for v in variants if '"' in v]
    return (quoted or variants or [""])[0]


def run(seeds: list[dict[str, str]], *, quota: int, stage: str) -> int:
    settings = Settings.load()
    routing = read_config(settings.config_path).llm_routing
    llm = build_client_for_stage(resolve_stage(routing, stage))
    openalex = _build_openalex(settings)

    base_hits = fix_hits = attempted = 0
    print(f"\nrecall@pool — {len(seeds)} seeds, quota={quota}, parse-stage={stage}\n")
    print(f"{'seed (expected id)':<40} {'base':>5} {'fix':>5}  surfaced-by")
    print("-" * 92)
    for seed in seeds:
        topic = seed["topic"]
        doi, arxiv = seed.get("doi", ""), seed.get("arxiv", "")
        intent = parse_intent(topic, [], llm=llm)
        if not intent.parse_ok:
            print(f"{topic[:40]:<40} {'—':>5} {'—':>5}  parse_error (skipped)")
            continue
        attempted += 1
        plan = build_query_plan(intent)
        fix_pool = federate(plan, openalex_client=openalex, quota=quota)
        base_pool = federate(_bag_only(plan), openalex_client=openalex, quota=quota)
        base = _matches(base_pool, doi, arxiv)
        fix = _matches(fix_pool, doi, arxiv)
        base_hits += bool(base)
        fix_hits += bool(fix)
        surfaced = _surfacing_variant(fix) if fix else ""
        label = (doi or arxiv)
        print(f"{(topic[:26] + '  ' + label)[:40]:<40} "
              f"{'✓' if base else '·':>5} {'✓' if fix else '·':>5}  {surfaced[:38]}")
    print("-" * 92)
    denom = attempted or 1
    print(f"\nrecall  baseline {base_hits}/{attempted} ({base_hits / denom:.0%})   "
          f"fix {fix_hits}/{attempted} ({fix_hits / denom:.0%})   "
          f"Δ +{fix_hits - base_hits}")
    # The baseline (bag + OpenAlex semantic) swings run-to-run: this bench fires
    # fix AND baseline back-to-back (~6 OpenAlex calls/seed) and OpenAlex throttles
    # the keyless semantic endpoint under that load. Production runs ONE federate.
    # The STABLE signal is the deterministic 'surfaced-by "…"' rows — a tight
    # quoted-phrase pass lands those even when the noisy channels collapse.
    print('note: trust the \'surfaced-by "quoted phrase"\' rows — those are the '
          "deterministic tight-lexical hits; baseline recall is semantic-endpoint noisy.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=Path, help="JSON list of {topic, doi?, arxiv?}; default = built-in")
    ap.add_argument("--quota", type=int, default=15, help="per-pass result cap (default 15)")
    ap.add_argument("--stage", default="deep_review", help="LLM stage for intent parse (default deep_review, matches run_screen)")
    args = ap.parse_args(argv)
    seeds = json.loads(args.seeds.read_text()) if args.seeds else _DEFAULT_SEEDS
    return run(seeds, quota=args.quota, stage=args.stage)


if __name__ == "__main__":
    raise SystemExit(main())
