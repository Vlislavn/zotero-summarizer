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

from dataclasses import dataclass, field
from typing import Any

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services._common import state
from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search._fulltext import acquire_full_text
from zotero_summarizer.services.search._models import Candidate, ResearchSession
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


@dataclass(slots=True)
class SearchDeps:
    """Process singletons the pipeline stages need (wired by ``default_deps``)."""

    llm: Any                       # deep-review-stage client (intent parse + targeted read)
    llm_light: Any                 # cheap feed-stage client (light-review quality)
    config: GoalsConfig
    reranker_model: str
    openalex_client: Any = None
    unpaywall_client: Any = None
    extractor: Any = None
    library_finder: LibraryFinder | None = None
    max_chars: int = 0             # quality-review text cap; 0 → config default
    quota: int = 15


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
        openalex_client=app.openalex_client,
        unpaywall_client=app.unpaywall_client,
        extractor=app.pdf_extractor,
        library_finder=None,
        max_chars=int(config.quality_review.max_text_chars),
    )


def run_screen(raw_query: str, questions: list[str], *, deps: SearchDeps) -> ResearchSession:
    """Phase 1: intent → plan → federate → score → rank → persist. Returns a saved
    ``ResearchSession`` with candidates ordered by the constrained contract."""
    intent = parse_intent(raw_query, questions, llm=deps.llm)
    plan = build_query_plan(intent)
    candidates = federate(
        plan, openalex_client=deps.openalex_client,
        library_finder=deps.library_finder, quota=deps.quota,
    )
    score_query_relevance(raw_query or intent.canonical_question, candidates, reranker_model=deps.reranker_model)
    rank_candidates(candidates)

    sess = session_store.new_session(raw_query=raw_query, intent=intent, plan=plan, questions=questions)
    sess.candidates = candidates
    sess.status = "screened"
    session_store.save(sess)
    return sess


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
    deep_set = select_deep_set(sess.candidates, k=k)
    for cand in deep_set:
        targeted_review(
            cand, full_text=fulltext.get(cand.candidate_id, ""), sections=[],
            query=sess.raw_query, questions=sess.questions, config=deps.config, llm=deps.llm,
        )

    sess.status = "reviewed"
    sess.screened_count = len(sess.candidates[:top_n])
    session_store.save(sess)
    return sess


__all__ = ["SearchDeps", "default_deps", "run_screen", "run_review", "LIGHT_REVIEW_N", "DEEP_SET_K"]
