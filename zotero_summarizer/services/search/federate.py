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

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zotero_summarizer.integrations.arxiv import search_arxiv
from zotero_summarizer.integrations.crossref import search_crossref
from zotero_summarizer.integrations.europepmc import search_europepmc
from zotero_summarizer.integrations.openreview import search_openreview
from zotero_summarizer.integrations.semantic_scholar import search_semantic_scholar
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.search._models import Candidate, Provenance
from zotero_summarizer.services.search.dedup import to_version_families

# A library candidate finder: takes the plan's library query, returns Candidates
# (already corpus-resolved). None when no corpus/library is available.
LibraryFinder = Callable[[str], list[Candidate]]


def _stamp(cands: list[Candidate], *, source: str, variant: str, at: str) -> list[Candidate]:
    for rank, cand in enumerate(cands):
        cand.provenance.append(
            Provenance(source=source, query_variant=variant, source_rank=rank, retrieved_at=at)
        )
    return cands


def _arxiv_channel(query: str, quota: int, at: str) -> list[Candidate]:
    hits = search_arxiv(query, max_results=quota)
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, arxiv_id=h.arxiv_id, doi=h.doi,
            authors=h.authors, year=h.year, venue="arXiv", url=h.pdf_url, is_open_access=True,
        )
        for h in hits
    ]
    return _stamp(cands, source="arxiv", variant=query, at=at)


def _europepmc_channel(query: str, quota: int, at: str) -> list[Candidate]:
    hits = search_europepmc(query, page_size=quota)
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi, pmid=h.pmid, pmcid=h.pmcid,
            authors=h.authors, year=h.year, venue=h.venue, is_open_access=h.is_open_access,
        )
        for h in hits
    ]
    return _stamp(cands, source="europepmc", variant=query, at=at)


def _openalex_channel(client: Any, query: str, quota: int, at: str, *, semantic: bool) -> list[Candidate]:
    if client is None or not query:
        return []
    hits = client.search_works(query, per_page=quota, semantic=semantic)
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi, openalex_id=h.openalex_id,
            authors=h.authors, year=h.year, venue=h.venue, is_open_access=h.is_oa,
            is_retracted=h.is_retracted, cited_by_count=h.cited_by_count,
        )
        for h in hits
    ]
    variant = f"{'semantic' if semantic else 'lexical'}:{query}"
    return _stamp(cands, source="openalex", variant=variant, at=at)


def _crossref_channel(query: str, quota: int, at: str, mailto: str | None) -> list[Candidate]:
    hits = search_crossref(query, rows=quota, mailto=mailto)
    cands = [
        Candidate(
            title=h.title, abstract=h.abstract, doi=h.doi,
            authors=h.authors, year=h.year, venue=h.venue,
        )
        for h in hits
    ]
    return _stamp(cands, source="crossref", variant=query, at=at)


def _semantic_scholar_channel(query: str, quota: int, at: str) -> list[Candidate]:
    hits = search_semantic_scholar(query, limit=quota)
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


def _openreview_channel(query: str, *, or_client: Any, openalex_client: Any, quota: int, at: str) -> list[Candidate]:
    hits = search_openreview(or_client, query, limit=quota)
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


def federate(
    plan: Any,
    *,
    openalex_client: Any = None,
    openreview_client: Any = None,
    library_finder: LibraryFinder | None = None,
    quota: int = 15,
    crossref_mailto: str | None = None,
) -> list[Candidate]:
    """Run every channel concurrently, quota each PASS, union into version families.

    Each lexical source may issue MORE than one pass — a tight quoted-phrase variant
    for precision plus the broad keyword bag for recall (``QueryPlan.*_variants``).
    ``to_version_families`` unions a paper found by several passes into one candidate;
    ranking never counts passes (``rank.py`` has no vote), so extra passes only enrich
    the pool. OpenAlex lexical is capped at 2 passes (keyless polite-pool budget)."""
    at = now_iso_z()
    tasks: list[Callable[[], list[Candidate]]] = []
    for q in _variant_queries(plan.arxiv_variants, plan.arxiv):
        tasks.append(lambda q=q: _arxiv_channel(q, quota, at))
    for q in _variant_queries(plan.europepmc_variants, plan.europepmc):
        tasks.append(lambda q=q: _europepmc_channel(q, quota, at))
    for q in _variant_queries(plan.openalex_lexical_variants, plan.openalex_lexical, cap=2):
        tasks.append(lambda q=q: _openalex_channel(openalex_client, q, quota, at, semantic=False))
    tasks.append(lambda: _openalex_channel(openalex_client, plan.openalex_semantic, quota, at, semantic=True))
    if plan.crossref:
        tasks.append(lambda: _crossref_channel(plan.crossref, quota, at, crossref_mailto))
    if plan.semantic_scholar:
        tasks.append(lambda: _semantic_scholar_channel(plan.semantic_scholar, quota, at))
    if plan.openreview and openreview_client is not None:
        tasks.append(lambda: _openreview_channel(
            plan.openreview, or_client=openreview_client, openalex_client=openalex_client, quota=quota, at=at
        ))
    if library_finder is not None:
        lib_query = plan.library_expanded or plan.library_raw
        tasks.append(lambda: _stamp(
            library_finder(lib_query), source="library", variant=lib_query, at=at
        ))

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        channel_results = [f.result() for f in [pool.submit(t) for t in tasks]]

    unioned = [cand for channel in channel_results for cand in channel]
    return to_version_families(unioned)


__all__ = ["federate", "LibraryFinder"]
