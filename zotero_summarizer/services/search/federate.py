"""Concurrent federation with per-channel quotas + provenance (spec §12, §13).

Fan out the query plan to every source channel AT ONCE (library + external run
concurrently — library-first is a UI ordering rule, not a sequential dependency),
map each hit to a ``Candidate`` stamped with its ``Provenance``, apply a per-channel
quota so a source queried with more variants can't out-vote the others, union into
version families, and return the deduped candidates.

The integration leaves own the network best-effort contract (a dead source returns
``[]``), so this module fans out and trusts the returned lists — an unexpected
exception from a channel propagates (fail-fast), it is not swallowed here.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zotero_summarizer.integrations.arxiv import search_arxiv
from zotero_summarizer.integrations.crossref import search_crossref
from zotero_summarizer.integrations.europepmc import search_europepmc
from zotero_summarizer.integrations.openreview import search_openreview
from zotero_summarizer.integrations.semantic_scholar import search_semantic_scholar
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services._common import settings
from zotero_summarizer.services._io import now_iso_z
from zotero_summarizer.services.search._models import Candidate, Provenance
from zotero_summarizer.services.search.dedup import to_version_families

# A library candidate finder: takes the plan's library query, returns Candidates
# (already corpus-resolved). None when no corpus/library is available.
LibraryFinder = Callable[[str], list[Candidate]]

LOGGER = logging.getLogger(__name__)

# Process-lazy keyless OpenAlex for Search federation — built once, reused (an
# OpenAlexClient owns an httpx pool; per-screen construction would leak one).
_SEARCH_OPENALEX: Any = None


def _search_openalex_client(app: Any, config: GoalsConfig) -> Any:
    """OpenAlex for Search, wired INDEPENDENTLY of prestige (correction #11): reuse
    the app's prestige client if present, else lazily build a keyless polite-pool
    client so the broadest source contributes even when prestige is disabled."""
    if app.openalex_client is not None:
        return app.openalex_client
    global _SEARCH_OPENALEX
    if _SEARCH_OPENALEX is None:
        from zotero_summarizer.integrations.openalex import OpenAlexClient
        from zotero_summarizer.integrations.openalex_cache import OpenAlexCache

        cache = app.openalex_cache or OpenAlexCache(settings().corpus_db_path)
        _SEARCH_OPENALEX = OpenAlexClient(cache, mailto=config.prestige.user_agent_email or None)
    return _SEARCH_OPENALEX


def _annotate_library_coverage(candidates: list[Candidate], reader: Any) -> None:
    """Resolve coverage over every retained DOI/arXiv in each version family.

    A hit owns its returned key. Only complete misses establish False; partial
    lookup failures remain unknown. No reader or supported ID starts no lookup.
    """
    if reader is None:
        return
    for cand in candidates:
        current = (cand.doi or None, cand.arxiv_id or None)
        queries = [current] if any(current) else []
        primary = {f"doi:{cand.doi}", f"arxiv:{cand.arxiv_id}"}
        for key in cand.identifier_keys():
            if key in primary:
                continue
            namespace, _, value = key.partition(":")
            if namespace == "doi":
                query = (value, None)
            elif namespace == "arxiv":
                query = (None, value)
            else:
                continue
            if query not in queries:
                queries.append(query)
        if not queries:
            continue
        incomplete, existing = False, None
        for doi, arxiv in queries:
            if arxiv and ("%" in arxiv or "_" in arxiv):
                incomplete = True
                arxiv = None
                if not doi:
                    continue
            try:
                existing = reader.find_by_external_id(doi=doi, arxiv_id=arxiv)
            except Exception as exc:  # noqa: BLE001 — external Zotero-read boundary, explicit unknown
                incomplete = True
                LOGGER.warning("library coverage lookup failed for %r; leaving unknown: %s", cand.title[:60], exc)
                continue
            if existing:
                break
        cand.in_library = True if existing else None if incomplete else False
        cand.existing_zotero_key = existing or None


def _stamp(cands: list[Candidate], *, source: str, variant: str, at: str) -> list[Candidate]:
    for rank, cand in enumerate(cands):
        cand.provenance.append(
            Provenance(source=source, query_variant=variant, source_rank=rank, retrieved_at=at)
        )
    return cands


def _arxiv_channel(query: str, quota: int, at: str, *, outcome: dict | None = None) -> list[Candidate]:
    hits = search_arxiv(query, max_results=quota, **({"outcome": outcome} if outcome is not None else {}))
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, arxiv_id=h.arxiv_id, doi=h.doi,
            authors=h.authors, year=h.year, venue="arXiv", url=h.pdf_url, is_open_access=True,
        )
        for h in hits
    ]
    return _stamp(cands, source="arxiv", variant=query, at=at)


def _europepmc_channel(query: str, quota: int, at: str, *, outcome: dict | None = None) -> list[Candidate]:
    hits = search_europepmc(query, page_size=quota, **({"outcome": outcome} if outcome is not None else {}))
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi, pmid=h.pmid, pmcid=h.pmcid,
            authors=h.authors, year=h.year, venue=h.venue, is_open_access=h.is_open_access,
            publication_types=list(h.publication_types),
        )
        for h in hits
    ]
    return _stamp(cands, source="europepmc", variant=query, at=at)


def _openalex_channel(client: Any, query: str, quota: int, at: str, *, semantic: bool, outcome: dict | None = None) -> list[Candidate]:
    if client is None or not query:
        if outcome is not None and client is None:
            outcome.update(status="not_attempted", count=None, error_code="client_unavailable")
        return []
    hits = client.search_works(query, per_page=quota, semantic=semantic,
                               **({"outcome": outcome} if outcome is not None else {}))
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi, openalex_id=h.openalex_id,
            authors=h.authors, year=h.year, venue=h.venue, is_open_access=h.is_oa,
            is_retracted=h.is_retracted, cited_by_count=h.cited_by_count,
            publication_types=list(h.publication_types),
            family_identifier_keys=getattr(h, "family_identifier_keys", []),
        )
        for h in hits
    ]
    variant = f"{'semantic' if semantic else 'lexical'}:{query}"
    return _stamp(cands, source="openalex", variant=variant, at=at)


def _crossref_channel(query: str, quota: int, at: str, mailto: str | None, *, outcome: dict | None = None) -> list[Candidate]:
    hits = search_crossref(query, rows=quota, mailto=mailto, **({"outcome": outcome} if outcome is not None else {}))
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi,
            authors=h.authors, year=h.year, venue=h.venue,
        )
        for h in hits
    ]
    return _stamp(cands, source="crossref", variant=query, at=at)


def _semantic_scholar_channel(query: str, quota: int, at: str, *, outcome: dict | None = None) -> list[Candidate]:
    hits = search_semantic_scholar(query, limit=quota, **({"outcome": outcome} if outcome is not None else {}))
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi, arxiv_id=h.arxiv_id, pmid=h.pmid,
            authors=h.authors, year=h.year, venue=h.venue, url=h.url, is_open_access=h.is_open_access,
        )
        for h in hits
    ]
    return _stamp(cands, source="semantic_scholar", variant=query, at=at)


def _openreview_openalex_id(openalex_client: Any, title: str, year: int | None) -> str:
    """Resolve a bare OpenAlex id for an OpenReview hit by title, so it can join
    the same version family as an arXiv/OpenAlex/Crossref hit of the same paper.
    No client, no match, or too-short/ambiguous title → "" (the candidate then
    falls back to its title-hash self-id; no cross-source merge, no re-rank
    effect either way — SIGNAL-ONLY)."""
    if openalex_client is None:
        return ""
    work = openalex_client.fetch_work_by_title(title, year=year)
    if work is None or not work.openalex_id:
        return ""
    return work.openalex_id.rsplit("/", 1)[-1]


def _openreview_channel(query: str, *, or_client: Any, openalex_client: Any, quota: int, at: str,
                        outcome: dict | None = None) -> list[Candidate]:
    hits = search_openreview(or_client, query, limit=quota, **({"outcome": outcome} if outcome is not None else {}))
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, authors=h.authors, year=h.year, venue=h.venue,
            url=h.url, doi=h.doi,
            openalex_id=_openreview_openalex_id(openalex_client, h.title, h.year),
            peer_review={
                "tier": h.tier, "venue": h.venue, "venueid": h.venueid,
                "mean_rating": h.mean_rating, "n_reviews": h.n_reviews,
            },
        )
        for h in hits
    ]
    return _stamp(cands, source="openreview", variant="topic", at=at)


def _variant_queries(variants: list[str], scalar: str, *, cap: int | None = None) -> list[str]:
    """Queries to issue for one lexical source: the plan's tight+bag ``*_variants``
    (order-preserving dedup, optionally capped), else the single ``scalar`` query — so
    a plan built before the recall fix (empty variants) issues exactly one pass as
    before. ``cap`` bounds passes on a budget-limited source (OpenAlex polite pool)."""
    queries = list(dict.fromkeys(v for v in variants if v)) if variants else ([scalar] if scalar else [])
    return queries[:cap] if cap else queries


def _matches_constraints(candidate: Candidate, plan: Any) -> bool:
    """Literal whole-token phrases, not a claim to classify study design semantically."""
    text = " " + " ".join(re.findall(r"\w+", f"{candidate.title} {candidate.abstract}".casefold())) + " "

    def contains(term: str) -> bool:
        tokens = re.findall(r"\w+", term.casefold())
        return bool(tokens) and (" " + " ".join(tokens) + " ") in text

    return (all(contains(term) for term in plan.must_include)
            and not any(contains(term) for term in plan.must_not_include)
            and (not plan.study_types or (
                any(contains(term) for term in plan.study_types)
                if plan.constraint_origin == "legacy_unknown" else
                bool(set(term.casefold() for term in plan.study_types)
                     & set(term.casefold() for term in candidate.publication_types)))))


def federate(
    plan: Any,
    *,
    openalex_client: Any = None,
    openreview_client: Any = None,
    library_finder: LibraryFinder | None = None,
    quota: int = 15,
    crossref_mailto: str | None = None,
    observe_outcomes: bool = False,
) -> list[Candidate]:
    """Run every channel concurrently, quota each PASS, union into version families.

    Each lexical source may issue MORE than one pass — a tight quoted-phrase variant
    for precision plus the broad keyword bag for recall (``QueryPlan.*_variants``).
    ``to_version_families`` unions a paper found by several passes into one candidate;
    ranking never counts passes (``rank.py`` has no vote), so extra passes only enrich
    the pool. OpenAlex lexical is capped at 2 passes (keyless polite-pool budget)."""
    at = now_iso_z()
    tasks: list[Callable[[], list[Candidate]]] = []
    requests: list[dict[str, Any]] = []
    def add_variant_tasks(source: str, queries: list[str], run: Callable[..., list[Candidate]], *, channel: str | None = None) -> None:
        if not queries:
            return
        allocations = [quota // len(queries)] * len(queries)
        for index in range(quota % len(queries)):
            allocations[index] += 1
        for query, limit in zip(queries, allocations):
            if limit:
                request = {"source": source, "query": query, "allocation": limit}
                receipt = {"status": "unknown", "count": None, "error_code": None} if observe_outcomes and channel else None
                if receipt is not None:
                    request.update(channel=channel, outcome=receipt)
                tasks.append(lambda q=query, n=limit, o=receipt: run(q, n, **({"outcome": o} if o is not None else {})))
                requests.append(request)

    add_variant_tasks(
        "arxiv", _variant_queries(plan.arxiv_variants, plan.arxiv),
        lambda q, n, **kw: _arxiv_channel(q, n, at, **kw), channel="arxiv",
    )
    add_variant_tasks(
        "europepmc", _variant_queries(plan.europepmc_variants, plan.europepmc),
        lambda q, n, **kw: _europepmc_channel(q, n, at, **kw), channel="europepmc",
    )
    add_variant_tasks(
        "openalex", _variant_queries(plan.openalex_lexical_variants, plan.openalex_lexical, cap=2),
        lambda q, n, **kw: _openalex_channel(openalex_client, q, n, at, semantic=False, **kw), channel="openalex_lexical",
    )
    add_variant_tasks("openalex", [plan.openalex_semantic] if plan.openalex_semantic else [],
                      lambda q, n, **kw: _openalex_channel(openalex_client, q, n, at, semantic=True, **kw),
                      channel="openalex_semantic")
    if plan.crossref:
        add_variant_tasks("crossref", [plan.crossref],
                          lambda q, n, **kw: _crossref_channel(q, n, at, crossref_mailto, **kw), channel="crossref")
    if plan.semantic_scholar:
        add_variant_tasks("semantic_scholar", [plan.semantic_scholar],
                          lambda q, n, **kw: _semantic_scholar_channel(q, n, at, **kw), channel="semantic_scholar")
    if plan.openreview and openreview_client is not None:
        add_variant_tasks("openreview", [plan.openreview], lambda q, n, **kw: _openreview_channel(
            q, or_client=openreview_client, openalex_client=openalex_client, quota=n, at=at, **kw), channel="openreview")
    if library_finder is not None:
        lib_query = plan.library_expanded or plan.library_raw
        add_variant_tasks("library", [lib_query], lambda q, n: _stamp(
            library_finder(q)[:n], source="library", variant=q, at=at))

    channel_results = []
    if tasks:
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            channel_results = [f.result() for f in [pool.submit(t) for t in tasks]]
    for request, results in zip(requests, channel_results):
        request.update(observations=len(results), status="observed" if results else "unknown")

    unioned = [cand for channel in channel_results for cand in channel]
    from zotero_summarizer.services.search._accounting import account_retrieval

    observed_counts = Counter(p.source for candidate in unioned for p in candidate.provenance)
    families = to_version_families(unioned)
    from zotero_summarizer.services.search._publication_types import recover_types

    recovery = recover_types(families, plan)
    accepted = [candidate for candidate in families if _matches_constraints(candidate, plan)]
    plan.retrieval_accounting = account_retrieval(observed_counts, families, accepted, plan, requests)
    plan.retrieval_accounting["type_recovery"] = recovery
    return accepted


__all__ = ["federate", "LibraryFinder"]
