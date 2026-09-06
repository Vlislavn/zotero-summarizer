"""Recall-fix invariants for Targeted Search (pure logic, no network / no LLM).

The gap this guards: `build_query_plan` used to collapse the parsed intent into ONE
keyword-bag string per lexical source, which OpenAlex's citation-weighted relevance
buries specific/named papers under. The fix issues a TIGHT quoted-phrase variant
alongside the bag and unions the passes (the reranker re-sorts). These tests pin the
three mechanical guarantees: the tight variant is built + distinct, the plan round-
trips (incl. old sessions with no variant keys), and `federate` issues one pass per
variant and unions a paper found by more than one pass.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.services.search import federate as fed
from zotero_summarizer.services.search._models import QueryPlan, SearchIntent
from zotero_summarizer.services.search.intent import _variants, build_query_plan

@pytest.fixture(autouse=True)
def _mock_other_sources(monkeypatch):
    monkeypatch.setattr(fed, "search_crossref", lambda *args, **kwargs: [])
    monkeypatch.setattr(fed, "search_semantic_scholar", lambda *args, **kwargs: [])

_CONCEPTS = [
    "llm-based agents", "evaluation methods", "benchmarks",
    "survey", "autonomous agents", "large language models",
]


def _intent() -> SearchIntent:
    return SearchIntent(
        raw_query="evaluation of llm agents",
        canonical_question="What benchmarks evaluate LLM-based agents?",
        concepts=list(_CONCEPTS),
        must_include=["agents"],
    )


# ── query construction ────────────────────────────────────────────────────────

def test_build_query_plan_emits_tight_quoted_variant_first() -> None:
    plan = build_query_plan(_intent())
    variants = plan.openalex_lexical_variants
    assert len(variants) == 2
    tight, bag = variants
    # tight = the user's topic as ONE exact-match quoted phrase (their words, not the
    # LLM-paraphrased concepts), distinct from the bag
    assert tight == '"evaluation of llm agents"'
    assert tight != bag
    assert bag == plan.openalex_lexical  # bag is the unchanged scalar
    # arXiv + Europe PMC carry the same tight-first shape
    assert plan.arxiv_variants[0] == tight
    assert plan.europepmc_variants[0] == tight


def test_tight_variant_is_leading_clause_of_topic() -> None:
    # the qualifier after ':' is dropped so the phrase stays title-like and matchable —
    # this is the exact shape that lands the CAPA-target survey (bag misses it, this hits)
    intent = SearchIntent(
        raw_query="evaluation of LLM-based agents: benchmarks and survey",
        concepts=["large language models", "benchmarks"],
    )
    assert build_query_plan(intent).openalex_lexical_variants[0] == '"evaluation of LLM-based agents"'


def test_tight_variant_strips_embedded_quotes() -> None:
    # a lone-token topic falls back to the longest multi-word concept; a stray double-
    # quote inside it would break the query grammar, so it's stripped
    intent = SearchIntent(raw_query="x", concepts=['ll"m agents', "eval"])
    tight = build_query_plan(intent).openalex_lexical_variants[0]
    assert tight == '"llm agents"'


def test_variants_helper_dedups_and_drops_empty() -> None:
    assert _variants("tight", "bag") == ["tight", "bag"]
    assert _variants("same", "same") == ["same"]   # tight == bag → one pass
    assert _variants("", "bag") == ["bag"]          # no tight → one pass


def test_single_concept_fallback_is_one_pass() -> None:
    # raw-query fallback: concepts == [raw]; tight == bag → a single query, no dup pass
    plan = build_query_plan(SearchIntent(raw_query="crispr", concepts=["crispr"]))
    assert plan.openalex_lexical_variants == [plan.openalex_lexical]


# ── persistence / back-compat ─────────────────────────────────────────────────

def test_query_plan_variants_round_trip() -> None:
    plan = build_query_plan(_intent())
    restored = QueryPlan.from_dict(plan.to_dict())
    assert restored.openalex_lexical_variants == plan.openalex_lexical_variants
    assert restored.arxiv_variants == plan.arxiv_variants


def test_old_session_without_variant_keys_loads_empty() -> None:
    # A session persisted before the recall fix has no *_variants keys at all.
    plan = build_query_plan(_intent())
    legacy = {k: v for k, v in plan.to_dict().items() if not k.endswith("_variants")}
    restored = QueryPlan.from_dict(legacy)
    assert restored.openalex_lexical_variants == []
    assert restored.openalex_lexical == plan.openalex_lexical  # scalar still there


def test_display_expands_lexical_variants() -> None:
    plan = build_query_plan(_intent())
    rows = plan.display()
    lex = [r["query"] for r in rows if r["source"] == "openalex (lexical)"]
    assert lex == plan.openalex_lexical_variants


# ── federation fan-out ────────────────────────────────────────────────────────

def _arxiv_hit(arxiv_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        title="A Survey on Evaluation of LLM-based Agents", abstract="",
        arxiv_id=arxiv_id, doi="", authors=[], year=2026, pdf_url="",
    )


class _FakeOpenAlex:
    def __init__(self) -> None:
        self.passes: list[tuple[str, bool]] = []

    def search_works(self, query: str, *, per_page: int = 15, semantic: bool = False) -> list:
        self.passes.append((query, semantic))
        return []


def test_federate_issues_one_pass_per_variant(monkeypatch) -> None:
    arxiv_q: list[str] = []
    epmc_q: list[str] = []
    monkeypatch.setattr(fed, "search_arxiv", lambda q, max_results=15: arxiv_q.append(q) or [])
    monkeypatch.setattr(fed, "search_europepmc", lambda q, page_size=15: epmc_q.append(q) or [])
    oa = _FakeOpenAlex()
    fed.federate(build_query_plan(_intent()), openalex_client=oa, quota=15)

    assert len(arxiv_q) == 2                          # tight + bag
    assert len(epmc_q) == 2
    lexical = [q for q, sem in oa.passes if not sem]
    semantic = [q for q, sem in oa.passes if sem]
    assert len(lexical) == 2                          # capped at 2 (polite-pool budget)
    assert len(semantic) == 1                         # semantic channel unchanged


def test_federate_unions_paper_found_by_multiple_passes(monkeypatch) -> None:
    # Every arXiv pass returns the SAME paper (same id) → version-family union → ONE candidate,
    # carrying one provenance stamp per pass that found it.
    monkeypatch.setattr(fed, "search_arxiv", lambda q, max_results=15: [_arxiv_hit("2503.16416")])
    monkeypatch.setattr(fed, "search_europepmc", lambda q, page_size=15: [])
    out = fed.federate(build_query_plan(_intent()), openalex_client=None, quota=15)

    survey = [c for c in out if c.arxiv_id == "2503.16416"]
    assert len(survey) == 1                                   # deduped, not double-counted
    arxiv_prov = [p for p in survey[0].provenance if p.source == "arxiv"]
    assert len(arxiv_prov) == 2                               # found by tight AND bag pass


def test_federate_falls_back_to_scalar_for_legacy_plan(monkeypatch) -> None:
    arxiv_q: list[str] = []
    monkeypatch.setattr(fed, "search_arxiv", lambda q, max_results=15: arxiv_q.append(q) or [])
    monkeypatch.setattr(fed, "search_europepmc", lambda q, page_size=15: [])
    # Legacy plan: scalar only, empty variant lists (a pre-fusion persisted session).
    legacy = QueryPlan(arxiv="agents survey", openalex_lexical="agents survey", europepmc="agents survey")
    fed.federate(legacy, openalex_client=None, quota=15)
    assert arxiv_q == ["agents survey"]                       # exactly one pass, the scalar
