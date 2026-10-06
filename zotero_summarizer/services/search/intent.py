"""Parse a raw topic into a structured intent + a PER-SOURCE query plan.

The expert review's first architectural fix (spec §13.1): one universal
reformulated query is wrong — different engines want different inputs. A cheap
``feed``-stage LLM produces a ``SearchIntent`` (canonical English question,
concepts, synonyms, must/must-not), and ``build_query_plan`` deterministically
derives a query string per source. The plan is shown to the user (transparency).

If the LLM output is empty or unparseable, ``parse_intent`` falls back to the raw
query for every field — the degradation the use-case spec's error table (§7)
explicitly requires ("reformulation empty/garbled → fall back to raw query").
"""
from __future__ import annotations

import logging
import re
from typing import Any

from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.search._models import SearchIntent, QueryPlan

LOGGER = logging.getLogger(__name__)

_PROMPT = """You are a scientific literature search planner. A researcher typed \
this topic (may be terse, RU/EN mixed, or a typo):

TOPIC: {topic}
{questions_block}
Return ONE JSON object with these keys:
- "canonical_question": one clear English sentence stating the information need \
(a paragraph-length restatement good for semantic search).
- "concepts": 3-8 core concept phrases (lowercase noun phrases).
- "synonyms": a FLAT array of alternative terms / acronyms (max 8 strings; do \
NOT nest objects).
- "related_terms": optional related identifiers or broader concepts, separate from aliases (max 8).
- "domain": the research domain supplied by the topic, or an empty string; never invent regulatory validity.
- "must_include": terms that MUST appear (empty array if none obvious).
- "must_not_include": terms to exclude (empty array if none).
- "study_types": relevant study/paper types if the topic implies them (e.g. \
"randomized controlled trial", "benchmark", "systematic review"); else empty.

Respond with MINIFIED JSON on a single line — no code fence, no prose, no \
nested objects, only flat string arrays."""

_STRICT_RETRY = (
    "\n\nYour previous answer was not valid JSON (fenced, truncated, or nested). "
    "Respond again with ONLY a single-line minified JSON object, flat string "
    "arrays, no code fence, no commentary."
)


def _as_str_list(value: Any) -> list[str]:
    """Coerce to a flat list of trimmed strings. Tolerates a dict-of-lists (a
    small model sometimes nests ``synonyms`` per concept despite the prompt)."""
    if isinstance(value, dict):
        value = [item for sub in value.values() for item in (sub if isinstance(sub, list) else [sub])]
    if not isinstance(value, list):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]


def _parse_once(prompt: str, *, llm: Any) -> dict[str, Any]:
    """One LLM call → parsed dict. Salvages a fenced / trailing-prose JSON blob
    (``extract_json_blob``); raises ``ValueError`` if nothing parseable came back."""
    return extract_json_blob(to_text(llm.prompt(prompt)))


def parse_intent(raw_query: str, questions: list[str], *, llm: Any) -> SearchIntent:
    """LLM feed-stage parse → SearchIntent. One strict retry when the model returns
    unparseable JSON (fenced/truncated/nested — small models do all three), THEN a
    raw-query fallback with ``parse_ok=False`` so a degraded plan is visible, never
    silent (spec §7 error contract)."""
    raw = (raw_query or "").strip()
    questions = [q.strip() for q in (questions or []) if q and q.strip()]
    fallback = SearchIntent(
        raw_query=raw, canonical_question=raw, concepts=[raw] if raw else [],
        questions=questions, parse_ok=False, constraint_origin="model_proposed",
    )
    if not raw:
        return fallback

    q_block = ""
    if questions:
        q_block = "The researcher also wants these questions answered:\n" + untrusted_input(
            "\n".join(f"- {q}" for q in questions)) + "\n"
    prompt = UNTRUSTED_INPUT_RULE + "\n\n" + _PROMPT.format(
        topic=untrusted_input(raw), questions_block=q_block)
    try:
        parsed = _parse_once(prompt, llm=llm)
    except ValueError:
        try:
            parsed = _parse_once(prompt + _STRICT_RETRY, llm=llm)
        except ValueError:
            LOGGER.warning("targeted_search.intent: unparseable after retry; raw-query fallback")
            return fallback
    canonical = (parsed.get("canonical_question") or "").strip()
    if not canonical:
        return fallback
    return SearchIntent(
        raw_query=raw,
        constraint_origin="model_proposed",
        canonical_question=canonical,
        concepts=_as_str_list(parsed.get("concepts")) or [raw],
        synonyms=_as_str_list(parsed.get("synonyms"))[:8],
        related_terms=_as_str_list(parsed.get("related_terms"))[:8],
        domain=parsed.get("domain", "") if isinstance(parsed.get("domain", ""), str) else "",
        must_include=_as_str_list(parsed.get("must_include")),
        must_not_include=_as_str_list(parsed.get("must_not_include")),
        study_types=_as_str_list(parsed.get("study_types")),
        questions=questions,
    )


def _tight_query(raw_query: str, concepts: list[str]) -> str:
    """A precision pass: ONE exact-match quoted phrase, not a conjunction of terms.

    The user's own leading clause (before any ``:``/``?``/em-dash qualifier) is the
    phrase, because it preserves their literal terminology — which tends to match paper
    *titles*, where a bag of LLM-paraphrased concepts does not (the parser turns
    "LLM-based agents" into "autonomous agents", so the literal title span never survives
    to be quoted; and a new low-citation paper then sinks under OpenAlex's citation-
    weighted bag ranking). A single contiguous phrase is specific enough that few works
    match, so citation weight can't bury the target — measured: bag + a 6-way quoted
    conjunction both miss "A Survey on Evaluation of LLM-based Agents", the phrase
    ``"evaluation of llm-based agents"`` lands it #2 (``+ survey`` → #1). Bare (no field
    prefix) → valid unchanged across OpenAlex ``search=``, Europe PMC, and arXiv ``all:``.

    Falls back to the longest multi-word concept when the topic is a lone token or a
    sentence too long to be a title phrase; "" (→ a single bag pass) when neither yields
    a multi-word phrase — quoting a lone token matches the same works as the bag."""
    head = re.split(r"[:?\n—–]", raw_query or "", maxsplit=1)[0]
    phrase = " ".join(head.replace('"', "").split())
    if 2 <= len(phrase.split()) <= 8:
        return f'"{phrase}"'
    multiword = [" ".join(c.replace('"', "").split()) for c in concepts if len(c.split()) >= 2]
    return f'"{max(multiword, key=lambda c: len(c.split()))}"' if multiword else ""


def _variants(tight: str, bag: str) -> list[str]:
    """Tight-first variant list for one lexical source, dropping an empty tight or a
    tight identical to the bag (e.g. a single-concept raw-query fallback → one pass)."""
    return [tight, bag] if tight and tight != bag else [bag]


def _constrained_lexical(query: str, intent: SearchIntent) -> str:
    """OpenAlex/Europe PMC Boolean syntax; all source results are also checked locally."""
    def quoted(term: str) -> str:
        return '"' + term.replace('\\', ' ').replace('"', ' ').strip() + '"'

    if intent.synonyms:
        query = "(" + " OR ".join([f"({query})", *(quoted(term) for term in intent.synonyms)]) + ")"
    clauses = [f"({query})"]
    clauses.extend(quoted(term) for term in intent.must_include)
    if intent.study_types and intent.constraint_origin == "legacy_unknown":
        clauses.append("(" + " OR ".join(quoted(term) for term in intent.study_types) + ")")
    constrained = " AND ".join(clauses)
    for term in intent.must_not_include:
        constrained += " NOT " + quoted(term)
    return constrained if len(clauses) > 1 or intent.must_not_include or intent.synonyms else query


def build_query_plan(intent: SearchIntent) -> QueryPlan:
    """Derive a source-specific query per channel (spec §13.1). Deterministic —
    no LLM. Lexical channels get concise concept terms; the semantic + library-
    expanded channels get the canonical paragraph. Each lexical source also carries
    a tight quoted-phrase variant (``*_variants``, tight-first) so federation issues
    a precision pass alongside the broad bag — the reranker re-sorts the union."""
    pending = dict(intent.pending_constraints)
    if intent.constraint_origin == "model_proposed":
        from dataclasses import replace

        pending.update({name: list(getattr(intent, name))
                        for name in ("must_include", "must_not_include", "study_types")})
        intent = replace(intent, must_include=[], must_not_include=[], study_types=[])
    concepts = intent.concepts or ([intent.raw_query] if intent.raw_query else [])
    lexical = " ".join(concepts[:6]).strip() or intent.raw_query
    arxiv_bag = " ".join(concepts[:5]).strip() or intent.raw_query
    tight = _tight_query(intent.raw_query, concepts)
    expanded = intent.canonical_question or intent.raw_query
    if concepts:
        expanded = (expanded + " " + " ".join(concepts[:6])).strip()
    semantic = intent.canonical_question or intent.raw_query
    domain_note = ""
    if intent.constraint_origin == "legacy_unknown":
        variants = [_constrained_lexical(query, intent) for query in _variants(tight, lexical)]
        europe_variants = variants
        arxiv_variants = _variants(tight, arxiv_bag)
    else:
        from zotero_summarizer.services.search._queries import complementary_queries, supplied_domain

        variants, europe_variants, arxiv_variants = complementary_queries(intent, tight)
        domain = supplied_domain(intent)
        domain_note = (f"Supplied in topic: {domain}" if domain else
                       f"Proposed, not a mandatory anchor: {intent.domain}" if intent.domain else
                       "No domain supplied; refine the topic if context needs disambiguation")
        # Raw user need, not the model's expanded canonical question, owns semantic scope.
        semantic = " ".join([intent.raw_query, *intent.questions]).strip()
        expanded = semantic
    lexical = _constrained_lexical(lexical, intent)
    return QueryPlan(
        constraint_origin=intent.constraint_origin,
        pending_constraints=pending,
        domain_note=domain_note,
        library_raw=intent.raw_query,
        library_expanded=expanded,
        openalex_lexical=lexical,
        openalex_semantic=semantic,
        europepmc=lexical,
        arxiv=arxiv_bag,
        crossref=" ".join([*concepts[:6], *intent.must_include, *intent.synonyms]),
        semantic_scholar=semantic,
        openreview=semantic,
        openalex_lexical_variants=variants,
        europepmc_variants=europe_variants,
        arxiv_variants=arxiv_variants,
        must_include=list(intent.must_include), must_not_include=list(intent.must_not_include),
        study_types=list(intent.study_types),
    )


__all__ = ["parse_intent", "build_query_plan"]
