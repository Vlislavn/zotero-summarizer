"""Targeted Search orchestrator — the two-phase pipeline (spec §5, §13.1).

    SCREEN (fast):  parse intent → per-source query plan → concurrent federation →
                    version-family dedup → cross-encoder query score → constrained rank
    REVIEW (slow):  light-review the top band (quality) → re-rank → select the deep
                    set → targeted deep read (query-lensed brief + grounded answers)

Split so the UI shows a ranked, deduped candidate list in seconds (SCREEN needs
only one intent LLM call + the local reranker), then the user triggers the
PDF+LLM-heavy REVIEW on demand — quality is measured on the top band BEFORE the
deep set is chosen (the expert review's quality-timing fix), so a rigorous paper
ranked 6th can still be pulled in for a deep read.

``SearchDeps`` bundles the process singletons; ``default_deps`` wires them from the
running app, and every stage takes them explicitly so the pipeline stays testable
with fakes (no hidden global reach inside the stages).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search._fulltext import acquire_full_text
from zotero_summarizer.services.search._models import Candidate, ResearchSession
from zotero_summarizer.services.search._relevance import attach_relevance
from zotero_summarizer.services.search._targeted_review import targeted_review
from zotero_summarizer.services.search.federate import LibraryFinder, federate
from zotero_summarizer.services.search.intent import build_query_plan, parse_intent
from zotero_summarizer.services.search.rank import rank_candidates, score_query_relevance
from zotero_summarizer.services.search.review import light_review, select_deep_set
from zotero_summarizer.settings import offline_requested

# Light-review the top band, then let quality reorder within it and the deep-set
# selector pull 3-4 for the full read (spec §5 steps 7a-7b). Named constants — a
# targeted session is a handful of papers, not a feed.
LIGHT_REVIEW_N = 10
DEEP_SET_K = 4

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchDeps:
    """Process singletons the pipeline stages need (wired by ``default_deps``)."""

    llm: Any                       # deep-review-stage client (intent parse + targeted read)
    llm_light: Any                 # cheap feed-stage client (light-review quality)
    config: GoalsConfig
    reranker_model: str
    openalex_client: Any = None
    openreview_client: Any = None  # SIGNAL-ONLY peer-review channel; None → channel skipped
    unpaywall_client: Any = None
    extractor: Any = None
    library_finder: LibraryFinder | None = None
    reader: Any = None             # ZoteroReader for library-coverage annotation; None → skip
    max_chars: int = 0             # quality-review text cap; 0 → config default
    quota: int = 15
    crossref_mailto: str | None = None  # Crossref polite-pool mailto (optional)


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

        cache = app.app_state.openalex_cache or OpenAlexCache(settings().corpus_db_path)
        _SEARCH_OPENALEX = OpenAlexClient(cache, mailto=config.prestige.user_agent_email or None)
    return _SEARCH_OPENALEX


def default_deps() -> SearchDeps:
    """Wire dependencies from the running app (mirrors ``deep_review._build_ctx``)."""
    app = state()
    config = app.app_state.config
    # ponytail: library channel deferred — federate() already supports a
    # LibraryFinder, but a corpus row carries no DOI/arXiv id so it can't
    # cross-source dedup yet (needs identifier columns on corpus_embeddings).
    # Wire build_library_finder here once those land; the pipeline is unchanged.
    return SearchDeps(
        llm=app.resolve_stage_client("deep_review"),
        llm_light=app.resolve_stage_client("feed"),
        config=config,
        reranker_model=config.corpus.reranker_model,
        openalex_client=_search_openalex_client(app, config),
        openreview_client=app.openreview_client,
        unpaywall_client=app.unpaywall_client,
        extractor=app.pdf_extractor,
        library_finder=None,
        reader=app.zotero_reader,
        max_chars=int(config.quality_review.max_text_chars),
        crossref_mailto=config.prestige.user_agent_email or None,
    )


def run_screen(raw_query: str, questions: list[str], *, deps: SearchDeps) -> ResearchSession:
    """Phase 1: intent → plan → federate → score → rank → persist. Returns a saved
    ``ResearchSession`` with candidates ordered by the constrained contract."""
    if offline_requested():
        from zotero_summarizer.api.errors import APIError

        raise APIError("strict_offline", "Targeted Search needs external literature sources", status_code=409)
    intent = parse_intent(raw_query, questions, llm=deps.llm)
    plan = build_query_plan(intent)
    candidates = federate(
        plan, openalex_client=deps.openalex_client,
        openreview_client=deps.openreview_client,
        library_finder=deps.library_finder, quota=deps.quota,
        crossref_mailto=deps.crossref_mailto,
    )
    score_query_relevance(raw_query or intent.canonical_question, candidates, reranker_model=deps.reranker_model)
    rank_candidates(candidates)
    attach_relevance(candidates)  # pool-relative band + why chips (no quality yet)
    _annotate_library_coverage(candidates, deps.reader)

    sess = session_store.new_session(raw_query=raw_query, intent=intent, plan=plan, questions=questions)
    sess.candidates = candidates
    sess.status = "screened"
    session_store.save(sess)
    return sess


def _annotate_library_coverage(candidates: list[Candidate], reader: Any) -> None:
    """Set each candidate's Zotero-library coverage in place (tri-state, fail-open).

    Mirrors triage ``_tick_dedup.dedup_against_library``: one reader across the
    batch, a per-candidate boundary guard. ``in_library`` becomes ``True`` (+ the
    found key) on a hit; ``False`` ONLY when the candidate carries a supported id
    (doi/arxiv) and the lookup returned None; otherwise it stays ``None``
    (unknown — a PMID/PMCID-only row or a failed lookup is never a false
    "missing"). ``reader is None`` (Zotero absent) leaves every candidate ``None``.
    """
    if reader is None:
        return
    for cand in candidates:
        doi = cand.doi or None
        arxiv = cand.arxiv_id or None
        if not doi and not arxiv:
            continue  # no supported id → coverage unknown (stays None)
        try:
            existing = reader.find_by_external_id(doi=doi, arxiv_id=arxiv)
        except Exception as exc:  # noqa: BLE001 — external Zotero-read boundary (see _tick_dedup)
            # fail-open: leave None (unknown), never a false "not in library".
            LOGGER.warning("library coverage lookup failed for %r; leaving unknown: %s", cand.title[:60], exc)
            continue
        if existing:
            cand.in_library = True
            cand.existing_zotero_key = existing
        else:
            cand.in_library = False


def _max_chars(deps: SearchDeps) -> int:
    return deps.max_chars or int(deps.config.quality_review.max_text_chars)


def run_review(session_id: str, *, deps: SearchDeps, top_n: int = LIGHT_REVIEW_N, k: int = DEEP_SET_K) -> ResearchSession:
    """Phase 2: light-review the top band, re-rank on quality, select + deep-read the
    set. Serial by design — each step downloads a PDF and calls the LLM, and stacking
    heavy local-model calls is unsafe on a unified-memory box (memory-safety rule)."""
    sess = session_store.load(session_id)
    fulltext: dict[str, str] = {}
    for cand in sess.candidates[:top_n]:
        text = acquire_full_text(cand, extractor=deps.extractor, unpaywall=deps.unpaywall_client)
        fulltext[cand.candidate_id] = text
        light_review(cand, full_text=text, sections=[], llm=deps.llm_light, max_chars=_max_chars(deps))

    rank_candidates(sess.candidates)  # quality now populated → re-rank the band
    attach_relevance(sess.candidates)  # re-derive bands/why now that quality chips exist
    # Persist the re-ranked, quality-scored band before the slow deep reads so the UI
    # shows bands immediately (save_merge keeps a concurrent Add's materialized key).
    session_store.save_merge(sess)

    deep_set = select_deep_set(sess.candidates, k=k)
    for cand in deep_set:
        targeted_review(
            cand, full_text=fulltext.get(cand.candidate_id, ""),
            query=sess.raw_query, questions=sess.questions, config=deps.config, llm=deps.llm,
        )
        session_store.save_merge(sess)  # incremental: each deep review appears as it lands

    sess.status = "reviewed"
    sess.screened_count = len(sess.candidates[:top_n])
    session_store.save_merge(sess)
    return sess


__all__ = ["SearchDeps", "default_deps", "run_screen", "run_review", "LIGHT_REVIEW_N", "DEEP_SET_K"]
