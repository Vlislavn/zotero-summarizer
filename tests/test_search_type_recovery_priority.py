import pytest

from zotero_summarizer.integrations.europepmc import EuropePmcHit
from zotero_summarizer.services.search import _publication_types as module
from zotero_summarizer.services.search._models import Candidate, QueryPlan
from zotero_summarizer.services.search.federate import _matches_constraints


@pytest.fixture
def lookup(monkeypatch):
    calls = []
    hits = {}
    def search(query, **kwargs):
        assert kwargs == {'page_size': 1}
        doi = query.removeprefix('DOI:"').removesuffix('"')
        calls.append(doi)
        return hits.get(doi, [])
    monkeypatch.setattr(module, 'search_europepmc', search)
    monkeypatch.setattr(module, 'offline_requested', lambda: False)
    monkeypatch.delenv('ZS_SEARCH_TYPE_LOOKUPS', raising=False)
    return calls, hits


def plan(kind='Systematic Review'):
    return QueryPlan(study_types=[kind], constraint_origin='user_confirmed')


@pytest.mark.parametrize('kind', ['Systematic Review', 'Randomized Controlled Trial'])
def test_late_title_hint_gets_default_lookup_budget(lookup, kind):
    calls, hits = lookup
    candidates = [Candidate(title='Related paper', doi=f'10.1000/{i}') for i in range(5)]
    target = Candidate(title=f'Device evidence: {kind}', doi='10.1000/target')
    candidates.append(target)
    hits[target.doi] = [EuropePmcHit(title='Source', abstract='', doi=target.doi, publication_types=[kind])]
    result = module.recover_types(candidates, plan(kind))
    assert calls == [target.doi, *[c.doi for c in candidates[:4]]]
    assert result['requests'] == result['budget'] == 5
    assert result['confirmed'] == 1
    assert result['unattempted'] == 1
    assert _matches_constraints(target, plan(kind))


@pytest.mark.parametrize('title', ['SYSTEMATIC REVIEW', 'systematic-review', 'systematic   review'])
def test_normalized_title_hint_only_allocates_lookup(lookup, title):
    calls, hits = lookup
    first = Candidate(title='Related', doi='10.1000/first')
    hinted = Candidate(title=title, doi='10.1000/hint')
    hits[hinted.doi] = [EuropePmcHit(title='Source', abstract='', doi=hinted.doi,
                                  publication_types=['Observational Study'])]
    result = module.recover_types([first, hinted], plan())
    assert calls == [hinted.doi, first.doi]
    assert result['confirmed'] == 0
    assert not _matches_constraints(hinted, plan())


def test_stable_groups_skip_adequate_and_non_doi(lookup):
    calls, _ = lookup
    rows = [Candidate(title='Systematic reviewer', doi='10.1000/partial'),
            Candidate(title='Related', abstract='Systematic Review', doi='10.1000/abstract'),
            Candidate(title='Systematic Review', doi='10.1000/hint1'),
            Candidate(title='Systematic Review', publication_types=[]),
            Candidate(title='Systematic Review', doi='10.1000/adequate', publication_types=['Systematic Review']),
            Candidate(title='Systematic-Review', doi='10.1000/hint2'),
            Candidate(title='Unrelated', doi='10.1000/last')]
    result = module.recover_types(rows, plan())
    assert calls == ['10.1000/hint1', '10.1000/hint2', '10.1000/partial', '10.1000/abstract', '10.1000/last']
    assert result['unattempted'] == result['confirmed'] == 0
    assert result['status'] == 'unknown'


def test_mismatched_doi_never_confirms(lookup):
    calls, hits = lookup
    candidate = Candidate(title='Systematic Review', doi='10.1000/target')
    hits[candidate.doi] = [EuropePmcHit(title='Source', abstract='', doi='10.1000/other',
                                     publication_types=['Systematic Review'])]
    assert module.recover_types([candidate], plan())['confirmed'] == 0
    assert candidate.publication_types == []
    assert not _matches_constraints(candidate, plan())


@pytest.mark.parametrize('budget', [0, 15])
def test_budget_bounds(lookup, monkeypatch, budget):
    calls, _ = lookup
    monkeypatch.setenv('ZS_SEARCH_TYPE_LOOKUPS', str(budget))
    rows = [Candidate(title='Systematic Review', doi=f'10.1000/{i}') for i in range(17)]
    result = module.recover_types(rows, plan())
    assert len(calls) == result['requests'] == budget
    assert result['unattempted'] == 17 - budget
    assert result['confirmed'] == 0


@pytest.mark.parametrize('budget', ['-1', '16', 'invalid', '1.5'])
def test_invalid_budget_fails_before_lookup(lookup, monkeypatch, budget):
    monkeypatch.setenv('ZS_SEARCH_TYPE_LOOKUPS', budget)
    with pytest.raises(ValueError):
        module.recover_types([Candidate(title='Systematic Review', doi='10.1000/one')], plan())
    assert lookup[0] == []


@pytest.mark.parametrize('mode', ['offline', 'legacy', 'default'])
def test_disabled_paths_preserve_no_lookup(lookup, monkeypatch, mode):
    request = plan()
    if mode == 'offline':
        monkeypatch.setattr(module, 'offline_requested', lambda: True)
    elif mode == 'legacy':
        request.constraint_origin = 'legacy_unknown'
    else:
        request = QueryPlan()
    monkeypatch.setenv('ZS_SEARCH_TYPE_LOOKUPS', 'invalid')
    result = module.recover_types([Candidate(title='Systematic Review', doi='10.1000/one')], request)
    assert result['requests'] == 0
    assert result['status'] == 'not_requested'
    assert lookup[0] == []
