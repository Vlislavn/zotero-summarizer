"""A146: parsed constraints must reach queries and constrain every source's hits."""

from zotero_summarizer.services.search import federate as federation
from zotero_summarizer.services.search._models import Candidate, QueryPlan, SearchIntent
from zotero_summarizer.services.search.intent import build_query_plan
from zotero_summarizer.services.search.refine import _delta_intent


def _intent():
    return SearchIntent(raw_query="cell therapy", concepts=["cell therapy"], synonyms=["CAR-T"],
                        must_include=["human"], must_not_include=["mouse"], study_types=["randomized trial"])


def test_plan_carries_executable_constraints_through_round_trip():
    plan = QueryPlan.from_dict(build_query_plan(_intent()).to_dict())
    assert plan.must_include == ["human"]
    assert plan.must_not_include == ["mouse"]
    assert plan.study_types == ["randomized trial"]
    assert "CAR-T" in plan.openalex_lexical
    assert 'NOT "mouse"' in plan.europepmc
    assert '"human"' in plan.openalex_lexical
    assert '"randomized trial"' in plan.openalex_lexical


def test_federation_enforces_constraints_even_when_source_ignores_them(monkeypatch):
    for name in ("_arxiv_channel", "_europepmc_channel", "_openalex_channel", "_crossref_channel",
                 "_semantic_scholar_channel"):
        monkeypatch.setattr(federation, name, lambda *args, **kwargs: [])
    candidates = [Candidate(title=text) for text in (
        "Human randomized trial of cell therapy", "Mouse and human randomized trial",
        "Human observational cell therapy", "Nonhuman randomized trial", "Unknown population",
    )]

    result = federation.federate(build_query_plan(_intent()), library_finder=lambda query: candidates)

    assert [candidate.title for candidate in result] == ["Human randomized trial of cell therapy"]


def test_agentic_drop_cannot_remove_an_explicit_must_include():
    delta = _delta_intent(_intent(), ["immune response"], ["human", "off topic"])
    assert delta.must_include == ["human"]
    assert "human" not in delta.must_not_include
    assert "off topic" in build_query_plan(delta).europepmc
