from zotero_summarizer.integrations.europepmc import _hit_from_result
from zotero_summarizer.services.search._models import SearchIntent
from zotero_summarizer.services.search.intent import build_query_plan


def test_primary_publication_types():
    hit = _hit_from_result({'title': 'Deep learning', 'pubTypeList': {'pubType': ['Review', 'Journal Article']}})
    assert hit.publication_types == ['Review', 'Journal Article']


def test_optional_expansion_is_separate():
    intent = SearchIntent(raw_query='validation: narrow devices', concepts=['validation', 'devices'],
                          related_terms=['ISO identifier'], domain='medical devices', constraint_origin='model_proposed')
    plan = build_query_plan(intent)
    assert plan.europepmc_variants[0] != plan.europepmc_variants[-1]
    assert 'ISO identifier' not in plan.europepmc_variants[0]
    assert 'narrow devices' in plan.openalex_semantic


def test_zero_channels_and_request_quota(monkeypatch):
    from zotero_summarizer.services.search import federate as module
    from zotero_summarizer.services.search._models import QueryPlan
    monkeypatch.setattr(module, '_arxiv_channel', lambda *args: [])
    plan = QueryPlan(arxiv_variants=['a', 'b', 'c'])
    assert module.federate(plan, quota=5) == []
    counts = plan.retrieval_accounting['arxiv']
    assert counts['observations'] == 0
    assert counts['status'] == 'unknown'
    assert [row['allocation'] for row in counts['variants']] == [2, 2, 1]
    assert all(row['status'] == 'unknown' for row in counts['variants'])
    assert QueryPlan.from_dict(plan.to_dict()).retrieval_accounting == plan.retrieval_accounting


def test_recover_metadata_only_for_explicit_filters(monkeypatch):
    from zotero_summarizer.services.search import _publication_types as module
    from zotero_summarizer.services.search._models import Candidate, QueryPlan
    from zotero_summarizer.integrations.europepmc import EuropePmcHit
    calls = []
    def lookup(query, **kwargs):
        calls.append(query)
        return [EuropePmcHit(title='Paper', abstract='', doi='10.1000/one', publication_types=['Review'])]
    monkeypatch.setattr(module, 'search_europepmc', lookup)
    monkeypatch.setattr(module, 'offline_requested', lambda: False)
    monkeypatch.setenv('ZS_SEARCH_TYPE_LOOKUPS', '1')
    candidates = [Candidate(title='One', doi='10.1000/one', publication_types=['article']),
                  Candidate(title='Two', doi='10.1000/two')]
    plan = QueryPlan(study_types=['review'], constraint_origin='user_confirmed')
    assert module.recover_types(candidates, plan)['requests'] == 1
    assert candidates[0].publication_types == ['article', 'Review']
    assert candidates[1].publication_types == []
    assert calls == ['DOI:"10.1000/one"']
    monkeypatch.setattr(module, 'offline_requested', lambda: True)
    module.recover_types(candidates, plan)
    module.recover_types(candidates, QueryPlan())
    assert len(calls) == 1


def test_refinement_and_persistence_preserve_domain_and_optional_terms():
    from zotero_summarizer.services.search.refine import _delta_intent
    base = SearchIntent(raw_query='device validation', domain='devices', related_terms=['ISO'],
                        constraint_origin='user_confirmed', must_not_include=['animal'])
    delta = _delta_intent(base, ['verification'])
    restored = SearchIntent.from_dict(delta.to_dict())
    assert restored.domain == 'devices'
    assert restored.related_terms == ['ISO']
    assert restored.must_not_include == ['animal']


def test_openalex_source_type_and_dedup():
    from zotero_summarizer.integrations.openalex import _search_hit_from_payload
    from zotero_summarizer.services.search._models import Candidate
    from zotero_summarizer.services.search.dedup import to_version_families
    hit = _search_hit_from_payload({'id':'https://openalex.org/W1', 'title':'Paper', 'type':'article'}, source_rank=0)
    assert hit.publication_types == ['article']
    family = to_version_families([Candidate(title='Paper', doi='10.1000/a', publication_types=['article']),
                                 Candidate(title='Paper', doi='10.1000/a', publication_types=['Review'])])[0]
    assert set(family.publication_types) == {'article', 'Review'}


def test_dedup_accounting_does_not_count_mutated_provenance_twice(monkeypatch):
    from zotero_summarizer.services.search import federate as module
    from zotero_summarizer.services.search._models import QueryPlan, Candidate, Provenance
    monkeypatch.setattr(module, '_arxiv_channel', lambda q, n, at: [
        Candidate(title='Same', doi='10.1000/same', provenance=[Provenance('arxiv', q)])])
    plan = QueryPlan(arxiv_variants=['first', 'second'])
    assert len(module.federate(plan, quota=2)) == 1
    assert plan.retrieval_accounting['arxiv']['observations'] == 2
    assert plan.retrieval_accounting['arxiv']['duplicate_observations'] == 1


def test_malformed_types_and_internal_shapes():
    import pytest
    from zotero_summarizer.services.search._models import Candidate
    assert _hit_from_result({'title': 'Paper', 'pubTypeList': {'pubType': 'Review'}}).publication_types == []
    with pytest.raises(ValueError):
        Candidate(title='Paper', publication_types='Review')
    with pytest.raises(ValueError):
        SearchIntent(raw_query='topic', related_terms='alias')
    with pytest.raises(ValueError):
        SearchIntent(raw_query='topic', domain=None)


def test_provider_syntax_keeps_optional_groups_and_explicit_narrowing_separate():
    intent = SearchIntent(raw_query='validation devices', concepts=['validation', 'devices', 'software', 'assurance'],
                          synonyms=['qualification'], related_terms=['ISO identifier'], domain='devices',
                          must_include=['implant'], must_not_include=['animal'], constraint_origin='user_confirmed')
    plan = build_query_plan(intent)
    assert len(plan.europepmc_variants) == 2
    assert all('"implant"' in query and 'NOT "animal"' in query for query in plan.europepmc_variants)
    assert all('all:"implant"' in query and 'NOT all:"animal"' in query for query in plan.arxiv_variants)
    # All bounded alternatives must survive the executable two-pass cap.
    assert 'assurance' in plan.europepmc_variants[1]
    assert 'qualification' in plan.europepmc_variants[1]
    assert 'ISO identifier' in plan.europepmc_variants[1]
    assert 'NOT' not in plan.openalex_semantic
