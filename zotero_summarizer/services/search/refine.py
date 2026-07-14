"""Agentic iterative refinement — pseudo-relevance-feedback rounds (spec Phase E).

Classic PRF / query-expansion loop: read the current top results, let an LLM name
the facets the pool UNDER-covers (``add_concepts``) and the terms that pulled in
off-topic drift (``drop_terms``), re-federate ONLY the delta concepts, union the new
candidates into the existing version families, re-score/re-rank/re-band the whole
pool, and persist a per-round summary. Bounded rounds; stop early when the strong
band stops changing or the LLM proposes no additions (convergence).

Off by default (``ZS_SEARCH_AGENTIC``) — each round costs one LLM call + a
federation pass. When on, ``run_agentic_rounds`` runs BEFORE the auto-review picks
the deep set, so the deep-4 is chosen from the refined pool ("E gates A's trigger").

The delta federation reuses the existing plan/federate/dedup/rank primitives — this
module only adds the LLM-driven concept delta and the union loop.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search._models import Candidate, ResearchSession, SearchIntent
from zotero_summarizer.services.search._relevance import attach_relevance
from zotero_summarizer.services.search.dedup import to_version_families
from zotero_summarizer.services.search.federate import federate
from zotero_summarizer.services.search.intent import _as_str_list, build_query_plan
from zotero_summarizer.services.search.rank import rank_candidates, score_query_relevance

LOGGER = logging.getLogger(__name__)

# A round reads the top few results and proposes a bounded delta. Named constants —
# a targeted session is a handful of papers, and an unbounded expansion would drift.
_REFINE_TOP_N = 8
_MAX_ADD = 4
_MAX_DROP = 3
_TOP_BAND_K = 4  # fallback stability window when no "strong" band was assigned

_REFINE_PROMPT = """You are refining a scientific literature search. The researcher's need:

NEED: {question}
CURRENT SEARCH CONCEPTS: {concepts}

The top results retrieved so far:
{results_block}

Judge whether these results actually match the need. Return ONE minified JSON object:
- "add_concepts": 0-4 NEW concept phrases (facets the results under-cover, or a more
  specific sub-topic worth fetching more of). Empty array if the top results fit well.
- "drop_terms": 0-3 terms that pulled in OFF-TOPIC results (steer the next fetch away).

Only add concepts clearly part of THIS need — do not drift into an adjacent field.
Respond with a single line of minified JSON, flat string arrays, no code fence, no prose."""


def agentic_enabled() -> bool:
    """True when ``ZS_SEARCH_AGENTIC`` is set truthy. Off by default (the extra LLM
    call + federation per round is opt-in, mirroring the other ``ZS_*`` feature flags)."""
    return os.environ.get("ZS_SEARCH_AGENTIC", "").strip().lower() in ("1", "true", "yes", "on")


def _result_line(cand: Candidate) -> str:
    meta = ", ".join(str(x) for x in (cand.venue, cand.year) if x)
    return f"- {cand.title}" + (f" ({meta})" if meta else "")


def refine_once(sess: ResearchSession, *, llm: Any) -> dict[str, list[str]]:
    """One LLM call → a plan delta ``{"add_concepts": [...], "drop_terms": [...]}``.

    An unparseable/garbled delta yields an EMPTY delta — the same visible-degradation
    contract ``parse_intent`` follows (spec §7): a bad LLM response means "no useful
    refinement this round", which naturally converges the loop, never a crash or a
    silently-wrong expansion. Documented boundary, not a blanket swallow."""
    top = sess.candidates[:_REFINE_TOP_N]
    if not top:
        return {"add_concepts": [], "drop_terms": []}
    prompt = _REFINE_PROMPT.format(
        question=sess.intent.canonical_question or sess.raw_query,
        concepts=", ".join(sess.intent.concepts) or sess.raw_query,
        results_block="\n".join(_result_line(c) for c in top),
    )
    try:
        parsed = extract_json_blob(to_text(llm.prompt(prompt)))
    except ValueError:
        LOGGER.warning("targeted_search.refine: unparseable delta; treating as no-op round (spec §7)")
        return {"add_concepts": [], "drop_terms": []}
    return {
        "add_concepts": _as_str_list(parsed.get("add_concepts"))[:_MAX_ADD],
        "drop_terms": _as_str_list(parsed.get("drop_terms"))[:_MAX_DROP],
    }


def _delta_intent(base: SearchIntent, add_concepts: list[str], drop_terms: list[str]) -> SearchIntent:
    """A focused intent for the delta federation: fetch the NEW concepts (lexical
    channels), anchored to the original question + new facets (semantic channel).

    ``drop_terms`` steer the delta AWAY (excluded from must-include, recorded in
    must-not-include) — they do NOT retroactively remove already-fetched candidates;
    the constrained rank already sinks off-topic hits by low ``query_score``."""
    drops = {d.lower() for d in drop_terms}
    canonical = base.canonical_question or base.raw_query
    if add_concepts:
        canonical = (canonical + " " + " ".join(add_concepts)).strip()
    return SearchIntent(
        raw_query=base.raw_query,
        canonical_question=canonical,
        concepts=list(add_concepts),
        synonyms=base.synonyms,
        must_include=[t for t in base.must_include if t.lower() not in drops],
        must_not_include=list(dict.fromkeys(base.must_not_include + drop_terms)),
        study_types=base.study_types,
        questions=base.questions,
    )


def _top_band_signature(candidates: list[Candidate]) -> frozenset[str]:
    """Membership of the strong band (order-insensitive) — the loop stops when this
    stops changing. Falls back to the top-K ids before any band is assigned."""
    strong = [c.candidate_id for c in candidates if c.relevance_band == "strong"]
    if not strong:
        strong = [c.candidate_id for c in candidates[:_TOP_BAND_K]]
    return frozenset(strong)


def run_agentic_rounds(session_id: str, *, deps: Any, max_rounds: int = 2) -> ResearchSession:
    """Run up to ``max_rounds`` PRF rounds on a screened session, persisting each.

    Per round: propose a concept delta → federate the delta → union into the existing
    families → re-score/rank/band the whole pool → record a summary. Stops early when
    the LLM proposes no additions or the strong band stabilizes (work-based budget, no
    wall-clock cap). Serial by design (each round federates + one LLM call)."""
    sess = session_store.load(session_id)
    query = sess.raw_query or sess.intent.canonical_question
    for rnd in range(1, max_rounds + 1):
        before = _top_band_signature(sess.candidates)
        delta = refine_once(sess, llm=deps.llm)
        add, drop = delta["add_concepts"], delta["drop_terms"]
        if not add:
            LOGGER.info("targeted_search.refine: round %d proposed no additions; converged", rnd)
            break

        delta_plan = build_query_plan(_delta_intent(sess.intent, add, drop))
        new_cands = federate(
            delta_plan, openalex_client=deps.openalex_client,
            library_finder=deps.library_finder, quota=deps.quota,
            crossref_mailto=deps.crossref_mailto,
        )
        before_ids = {c.candidate_id for c in sess.candidates}
        unioned = to_version_families(sess.candidates + new_cands)
        score_query_relevance(query, unioned, reranker_model=deps.reranker_model)
        rank_candidates(unioned)
        attach_relevance(unioned)
        sess.candidates = unioned

        added = len([c for c in unioned if c.candidate_id not in before_ids])
        sess.refinements.append({
            "round": rnd, "add_concepts": add, "drop_terms": drop,
            "new_candidates": added, "pool_size": len(unioned),
        })
        sess = session_store.save_merge(sess)
        LOGGER.info("targeted_search.refine: round %d +%d new (pool=%d)", rnd, added, len(unioned))
        if _top_band_signature(sess.candidates) == before:
            LOGGER.info("targeted_search.refine: strong band stable after round %d; stopping", rnd)
            break
    return sess


__all__ = ["agentic_enabled", "refine_once", "run_agentic_rounds"]
