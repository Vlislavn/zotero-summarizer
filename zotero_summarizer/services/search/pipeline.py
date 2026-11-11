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
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services._common import deep_review_fleet_concurrency, settings, state
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search import require_online
from zotero_summarizer.services.search._fulltext import acquire_full_text
from zotero_summarizer.services.search._models import Candidate, ResearchSession, ScreenRequest, ConfirmedConstraints
from zotero_summarizer.services.search._relevance import attach_relevance
from zotero_summarizer.services.search._question_evidence import run_question_evidence
from zotero_summarizer.services.search._question_policy import apply_question_policy
from zotero_summarizer.services.search._targeted_review import targeted_review
from zotero_summarizer.services.search.federate import LibraryFinder, federate
from zotero_summarizer.services.search.intent import build_query_plan, parse_intent
from zotero_summarizer.services.search.rank import rank_candidates, score_query_relevance
from zotero_summarizer.services.search.review import light_review, select_deep_set

# Light-review the top band, then let quality reorder within it and the deep-set
# selector pull 3-4 for the full read (spec §5 steps 7a-7b). Named constants — a
# targeted session is a handful of papers, not a feed.
LIGHT_REVIEW_N = 10
DEEP_SET_K = 4
MAX_PAPER_CONCURRENCY = 4  # papers reviewed at once (a remote provider's own cap still applies; local → 1)

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
    paper_concurrency: int = 1     # papers light-/deep-reviewed at once; 1 = serial
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

        cache = app.openalex_cache or OpenAlexCache(settings().corpus_db_path)
        _SEARCH_OPENALEX = OpenAlexClient(cache, mailto=config.prestige.user_agent_email or None)
    return _SEARCH_OPENALEX


def _paper_concurrency(app: Any) -> int:
    """Papers reviewed at once: ``MAX_PAPER_CONCURRENCY`` bounded by each review stage's
    provider (a local model stays serial; a remote one honours its own cap)."""
    return min(deep_review_fleet_concurrency(app.resolve_stage_provider(stage), MAX_PAPER_CONCURRENCY)
               for stage in ("deep_review", "feed"))


def default_deps() -> SearchDeps:
    """Wire dependencies from the running app (mirrors ``deep_review._build_ctx``)."""
    require_online()
    app = state()
    config = app.app_state.config
    # ponytail: library channel deferred — federate() already supports a
    # LibraryFinder, but a corpus row carries no DOI/arXiv id so it can't
    # cross-source dedup yet (needs identifier columns on corpus_embeddings).
    # Wire build_library_finder here once those land; the pipeline is unchanged.
    return SearchDeps(
        llm=factory.resolve_stage_client(app, "deep_review"),
        llm_light=factory.resolve_stage_client(app, "feed"),
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
        paper_concurrency=_paper_concurrency(app),
    )


def run_screen(raw_query: str, questions: list[str], *, deps: SearchDeps, constraints: ConfirmedConstraints | None = None,
               evidence_policy: str = "baseline") -> ResearchSession:
    """Phase 1: intent → plan → federate → score → rank → persist. Returns a saved
    ``ResearchSession`` with candidates ordered by the constrained contract."""
    request = ScreenRequest(query=raw_query, questions=questions, evidence_policy=evidence_policy)
    raw_query, questions = request.query, request.questions
    require_online()
    intent = parse_intent(raw_query, questions, llm=deps.llm)
    if constraints is not None:
        intent.pending_constraints = {name: list(getattr(intent, name))
                                      for name in ("must_include", "must_not_include", "study_types")}
        for name, values in constraints.model_dump().items():
            setattr(intent, name, values)
        intent.constraint_origin = "user_confirmed"
    plan = build_query_plan(intent)
    candidates = federate(
        plan, openalex_client=deps.openalex_client,
        openreview_client=deps.openreview_client,
        library_finder=deps.library_finder, quota=deps.quota,
        crossref_mailto=deps.crossref_mailto, observe_outcomes=True,
    )
    score_query_relevance(raw_query or intent.canonical_question, candidates, reranker_model=deps.reranker_model)
    rank_candidates(candidates)
    attach_relevance(candidates)  # pool-relative band + why chips (no quality yet)
    _annotate_library_coverage(candidates, deps.reader)

    sess = session_store.new_session(raw_query=raw_query, intent=intent, plan=plan, questions=questions)
    sess.candidates = candidates
    sess.evidence_policy = request.evidence_policy
    sess.status = "screened"
    session_store.save(sess)
    return sess


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


def _max_chars(deps: SearchDeps) -> int:
    return deps.max_chars or int(deps.config.quality_review.max_text_chars)


def _for_each(items: list[Candidate], work: Any, workers: int) -> None:
    """Run ``work(item)`` over ``items`` — in order when serial, else ``workers`` at once.
    ``work`` isolates its own per-paper failures."""
    if workers <= 1 or len(items) <= 1:
        for item in items:
            work(item)
        return
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="search-review") as pool:
        list(pool.map(work, items))


def run_review(session_id: str, *, deps: SearchDeps, top_n: int = LIGHT_REVIEW_N, k: int = DEEP_SET_K) -> ResearchSession:
    """Phase 2: light-review the top band, re-rank on quality, select + deep-read the
    set. Papers run ``deps.paper_concurrency`` at a time (1 for a local model — stacking
    heavy local calls is unsafe on a unified-memory box; remote: up to
    ``MAX_PAPER_CONCURRENCY``); ``screened_count`` counts light-reviewed papers as they land."""
    require_online()
    sess = session_store.load(session_id)
    if sess.discovery:
        from zotero_summarizer.api.errors import APIError
        from zotero_summarizer.services.search.discovery import require_current_discovery

        if sess.status != "reviewing":
            raise APIError("explicit_review_required", "Discovery requires an explicit Review claim", status_code=409)
        require_current_discovery(sess)
    request = ScreenRequest(query=sess.raw_query, questions=sess.questions, evidence_policy=sess.evidence_policy)
    fulltext: dict[str, str] = {}
    run_question_evidence(sess, deps, fulltext)
    sess.screened_count = 0
    progress = threading.Lock()

    def light_one(cand: Candidate) -> None:
        if session_store.is_deleted(session_id):
            return
        try:
            text = fulltext.get(cand.candidate_id)
            if text is None:
                text = acquire_full_text(cand, extractor=deps.extractor, unpaywall=deps.unpaywall_client)
            fulltext[cand.candidate_id] = text
            light_review(cand, full_text=text, sections=[], llm=deps.llm_light, max_chars=_max_chars(deps))
        except Exception as exc:  # noqa: BLE001 — isolate failures to this paper
            cand.review = {"state": "error", "error": str(exc)[:500]}
        with progress:  # "reviewed N of M": the UI reads screened_count from the polled session
            sess.screened_count += 1
            session_store.save_merge(sess)

    _for_each(sess.candidates[:top_n], light_one, deps.paper_concurrency)
    if session_store.is_deleted(session_id):
        return sess

    rank_candidates(sess.candidates)  # quality now populated → re-rank the band
    apply_question_policy(sess)
    attach_relevance(sess.candidates)  # re-derive bands/why now that quality chips exist
    # Persist the re-ranked, quality-scored band before the slow deep reads so the UI
    # shows bands immediately (save_merge keeps a concurrent Add's materialized key).
    session_store.save_merge(sess)

    deep_set = select_deep_set(sess.candidates, k=k)

    def deep_one(cand: Candidate) -> None:
        if session_store.is_deleted(session_id) or (cand.review and cand.review.get("state") == "error"):
            return
        try:
            targeted_review(
                cand, full_text=fulltext.get(cand.candidate_id, ""),
                query=request.query, questions=request.questions, config=deps.config, llm=deps.llm,
            )
        except Exception as exc:  # noqa: BLE001 — isolate failures to this paper
            cand.review = {"state": "error", "error": str(exc)[:500]}
        session_store.save_merge(sess)  # incremental: each deep review appears as it lands

    _for_each(deep_set, deep_one, deps.paper_concurrency)

    if session_store.is_deleted(session_id):
        return sess
    sess.status = "reviewed"
    sess.screened_count = len(sess.candidates[:top_n])
    session_store.save_merge(sess)
    return sess


__all__ = ["SearchDeps", "default_deps", "run_screen", "run_review", "LIGHT_REVIEW_N", "DEEP_SET_K"]
