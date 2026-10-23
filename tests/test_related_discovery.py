"""Discovery shares Search work but cannot auto-review or materialize anything."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.routes import library
from zotero_summarizer.services.search import discovery, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, SearchIntent


def setup_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(session, 'settings', lambda: SimpleNamespace(search_dir=tmp_path / 'search'))
    context = {'seed_key': 'PAPER001', 'seed': {'title': 'Public seed', 'doi': '10.1/seed'},
               'query': 'Public seed\nPublic abstract', 'fingerprint': 'initial', 'aliases': {'PAPER001'},
               'items': {'PAPER001': {'title': 'Public seed', 'doi': '10.1/seed'},
                         'PAPER002': {'title': 'Saved version', 'doi': '10.1/saved'}}}
    monkeypatch.setattr(discovery, 'discovery_context', lambda key: context)
    monkeypatch.setattr(discovery, 'require_online', lambda: None)
    monkeypatch.setattr(discovery, 'default_deps', lambda: SimpleNamespace(observe_source_outcomes=False))
    calls = []

    def screen(query, questions, **kwargs):
        calls.append((query, questions))
        assert kwargs['deps'].observe_source_outcomes is True
        sess = session.new_session(raw_query=query, questions=[], intent=SearchIntent(raw_query=query),
                                   plan=QueryPlan(retrieval_accounting=context.get('outcomes', {})))
        sess.status = 'screened'
        sess.candidates = [Candidate(title='Seed', doi='10.1/seed'),
                           Candidate(title='Saved alias', doi='10.1/saved'),
                           Candidate(title='Already in Zotero', doi='10.1/other', in_library=True),
                           Candidate(title='New, old publication', doi='10.1/new', year=1995, in_library=False),
                           Candidate(title='Unresolved membership')]
        session.save(sess)
        return sess

    monkeypatch.setattr(discovery, 'run_screen', screen)
    return context, calls


def test_http_discovery_is_explicit_persisted_and_does_not_start_review(tmp_path, monkeypatch):
    context, calls = setup_discovery(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(library.router)

    response = TestClient(app).post('/api/library/related/PAPER001/discover')

    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'screened'
    assert calls == [('Public seed\nPublic abstract', [])]
    assert [row['title'] for row in result['candidates']] == ['New, old publication', 'Unresolved membership']
    assert result['discovery']['excluded_saved'] == 3
    assert result['discovery']['membership_unknown'] == 1
    assert all(row['review'] == {} and row['materialized_zotero_key'] == '' for row in result['candidates'])
    assert session.load(result['id']).discovery == result['discovery']


def test_repeat_clicks_share_persisted_work_and_context_changes_invalidate(tmp_path, monkeypatch):
    context, calls = setup_discovery(tmp_path, monkeypatch)
    first = discovery.find_new('PAPER001')
    assert discovery.find_new('PAPER001')['id'] == first['id']
    assert len(calls) == 1
    context['fingerprint'] = 'same-count-note-edited'
    second = discovery.find_new('PAPER001')
    assert second['id'] != first['id']
    assert len(calls) == 2


def test_concurrent_clicks_share_one_screen(tmp_path, monkeypatch):
    context, calls = setup_discovery(tmp_path, monkeypatch)
    barrier = Barrier(2)

    def click():
        barrier.wait()
        return discovery.find_new('PAPER001')['id']

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: click(), range(2)))
    assert len(set(results)) == 1
    assert len(calls) == 1


def test_legacy_session_wire_does_not_gain_discovery_key(tmp_path, monkeypatch):
    setup_discovery(tmp_path, monkeypatch)
    sess = session.new_session(raw_query='Topic', questions=[], intent=SearchIntent(raw_query='Topic'), plan=QueryPlan())
    assert 'discovery' not in sess.to_dict()
    assert type(sess).from_dict(sess.to_dict()).to_dict() == sess.to_dict()


def test_public_query_never_uses_private_detail_fields():
    from zotero_summarizer.services.library.related import public_query

    query = public_query({'title': 'Public', 'abstract': 'Abstract', 'notes': ['PRIVATE'],
                          'user_note': 'PRIVATE', 'annotations': [{'comment': 'PRIVATE'}]})
    assert query == 'Public\nAbstract'
    assert 'PRIVATE' not in query


def test_provider_failures_are_persisted_separately_and_explicit_retry_supersedes_snapshot(tmp_path, monkeypatch):
    context, calls = setup_discovery(tmp_path, monkeypatch)
    context['outcomes'] = {
        'arxiv': {'variants': [{'outcome': {'provider': 'arxiv', 'status': 'success_empty', 'count': 0, 'error_code': None}}]},
        'semantic_scholar': {'variants': [{'outcome': {'provider': 'semantic_scholar', 'status': 'failed',
                                                     'count': None, 'error_code': 'rate_limited'}}]},
    }
    first = discovery.find_new('PAPER001')
    assert first['discovery']['provider_availability'] == 'partial_failure'
    assert discovery.find_new('PAPER001')['id'] == first['id']
    second = discovery.find_new('PAPER001', refresh=True)
    assert second['id'] != first['id']
    assert discovery.find_new('PAPER001')['id'] == second['id']
    assert session.load(first['id']).discovery['superseded_by'] == second['id']
    assert len(calls) == 2
    assert [row['status'] for row in second['discovery']['source_outcomes']] == ['success_empty', 'failed']


@pytest.mark.parametrize('provider,module_name,function_name,payload', [
    ('arxiv', 'arxiv', 'search_arxiv', '<feed xmlns="http://www.w3.org/2005/Atom"/>'),
    ('crossref', 'crossref', 'search_crossref', {'message': {'items': []}}),
    ('europepmc', 'europepmc', 'search_europepmc', {'resultList': {'result': []}}),
    ('semantic_scholar', 'semantic_scholar', 'search_semantic_scholar', {'data': []}),
    ('openalex', 'openalex', 'search_works', {'results': []}),
])
@pytest.mark.parametrize('status,expected', [(200, 'success_empty'), (429, 'failed')])
def test_real_source_leaves_distinguish_empty_from_http_failure_without_network(
    monkeypatch, provider, module_name, function_name, payload, status, expected,
):
    from importlib import import_module
    from zotero_summarizer.integrations.search_outcomes import capture_source_outcome

    module = import_module(f'zotero_summarizer.integrations.{module_name}')
    monkeypatch.setattr(module._RATE_LIMITER, 'acquire', lambda: None)
    response = httpx.Response(status, text=payload) if isinstance(payload, str) else httpx.Response(status, json=payload)
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        call = (module.OpenAlexClient(SimpleNamespace(), http_client=client).search_works
                if provider == 'openalex' else lambda query: getattr(module, function_name)(query, http_client=client))
        with capture_source_outcome(provider) as receipt:
            assert call('public synthetic topic') == []
    assert receipt['status'] == expected
    assert receipt['count'] == (0 if status == 200 else None)
    assert receipt['error_code'] == (None if status == 200 else 'rate_limited')
    assert set(receipt) == {'provider', 'status', 'count', 'error_code'}
    assert 'public synthetic topic' not in str(receipt)


def test_existing_federation_executor_binds_and_isolates_each_request_outcome(monkeypatch):
    import importlib
    from zotero_summarizer.integrations.search_outcomes import record_source_outcome

    federation = importlib.import_module('zotero_summarizer.services.search.federate')
    barrier = Barrier(2)

    def leaf(query, **kwargs):
        barrier.wait()
        if query == 'A':
            record_source_outcome('arxiv', 'success_empty', count=0)
        else:
            record_source_outcome('arxiv', 'failed', error_code='transport_error')
        return []

    monkeypatch.setattr(federation, 'search_arxiv', leaf)
    plans = [QueryPlan(arxiv='A'), QueryPlan(arxiv='B')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda plan: federation.federate(plan, observe_outcomes=True), plans)) == [[], []]
    receipts = [plan.retrieval_accounting['arxiv']['variants'][0]['outcome'] for plan in plans]
    assert [row['status'] for row in receipts] == ['success_empty', 'failed']
    assert [row['count'] for row in receipts] == [0, None]
    monkeypatch.setattr(federation, 'search_arxiv', lambda *args, **kwargs: [])
    ordinary = QueryPlan(arxiv='ordinary')
    assert federation.federate(ordinary) == []
    assert 'outcome' not in ordinary.retrieval_accounting['arxiv']['variants'][0]
    unknown = QueryPlan(arxiv='uninstrumented')
    assert federation.federate(unknown, observe_outcomes=True) == []
    assert unknown.retrieval_accounting['arxiv']['variants'][0]['outcome']['status'] == 'unknown'


def test_outcome_capture_resets_and_rejects_raw_diagnostics():
    from zotero_summarizer.integrations.search_outcomes import capture_source_outcome, record_source_outcome

    with capture_source_outcome('arxiv') as outer:
        with capture_source_outcome('crossref') as inner:
            record_source_outcome('arxiv', 'failed', error_code='transport_error')
        assert inner['status'] == 'unknown'
        record_source_outcome('arxiv', 'success_empty', count=0)
        with pytest.raises(ValueError, match='Invalid source outcome code'):
            record_source_outcome('arxiv', 'failed', error_code='raw URL or exception')
    record_source_outcome('arxiv', 'failed', error_code='transport_error')
    assert outer['status'] == 'success_empty'


def test_discovery_skips_listed_session_deleted_before_locked_load(tmp_path, monkeypatch):
    context, calls = setup_discovery(tmp_path, monkeypatch)
    unrelated = session.new_session(raw_query='Unrelated topic', questions=[],
                                    intent=SearchIntent(raw_query='Unrelated topic'), plan=QueryPlan())
    session.save(unrelated)
    original_list = session.list_sessions

    def list_then_delete():
        summaries = original_list()
        assert any(row['id'] == unrelated.id for row in summaries)
        session.delete(unrelated.id)
        return summaries

    monkeypatch.setattr(session, 'list_sessions', list_then_delete)

    result = discovery.find_new('PAPER001')

    assert session.is_deleted(unrelated.id)
    assert session.load(result['id']).discovery == result['discovery']
    assert [row['title'] for row in result['candidates']] == ['New, old publication', 'Unresolved membership']
    assert len(calls) == 1


def test_retry_keeps_replacement_usable_when_prior_deleted_after_replacement_save(tmp_path, monkeypatch):
    context, calls = setup_discovery(tmp_path, monkeypatch)
    first = discovery.find_new('PAPER001')
    original_save_merge = session.save_merge

    def save_then_delete_prior(replacement):
        saved = original_save_merge(replacement)
        assert session.load(replacement.id).id == replacement.id
        session.delete(first['id'])
        return saved

    monkeypatch.setattr(session, 'save_merge', save_then_delete_prior)

    replacement = discovery.find_new('PAPER001', refresh=True)

    assert replacement['id'] != first['id']
    assert session.is_deleted(first['id'])
    with pytest.raises(FileNotFoundError):
        session.load(first['id'])
    assert session.load(replacement['id']).discovery == replacement['discovery']
    assert discovery.find_new('PAPER001')['id'] == replacement['id']
    assert len(calls) == 2


def test_session_skip_is_tombstone_only_and_default_errors_remain_loud(tmp_path, monkeypatch):
    setup_discovery(tmp_path, monkeypatch)
    stored = session.new_session(raw_query='Topic', questions=[], intent=SearchIntent(raw_query='Topic'), plan=QueryPlan())
    session.save(stored)
    session.delete(stored.id)
    mutations = []

    assert session.load_unless_deleted(stored.id) is None
    assert session.update(stored.id, mutations.append, skip_deleted=True) is None
    assert mutations == []
    with pytest.raises(FileNotFoundError):
        session.update(stored.id, mutations.append)
    with pytest.raises(FileNotFoundError):
        session.load_unless_deleted('untombstoned-missing')
    with pytest.raises(FileNotFoundError):
        session.update('untombstoned-missing', mutations.append, skip_deleted=True)
    session._path('corrupt').write_text('{broken', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        session.load_unless_deleted('corrupt')
    with pytest.raises(json.JSONDecodeError):
        session.update('corrupt', mutations.append, skip_deleted=True)
    assert mutations == []


def test_identifier_only_ui_fixture_matches_real_channel_candidate_projection(monkeypatch):
    from importlib import import_module
    from zotero_summarizer.integrations.crossref import CrossrefHit
    from zotero_summarizer.integrations.europepmc import EuropePmcHit
    from zotero_summarizer.integrations.openalex import OpenAlexSearchHit

    federation = import_module('zotero_summarizer.services.search.federate')
    fixture = Path(__file__).resolve().parents[1] / 'frontend/src/components/paper/fixtures/discoveryChannelCandidates.json'
    expected = json.loads(fixture.read_text(encoding='utf-8'))
    crossref, pubmed, openalex = expected
    monkeypatch.setattr(federation, 'search_crossref', lambda *args, **kwargs: [CrossrefHit(
        title=crossref['title'], abstract=crossref['abstract'], doi='https://doi.org/10.1234/Fixture-DOI')])
    monkeypatch.setattr(federation, 'search_europepmc', lambda *args, **kwargs: [EuropePmcHit(
        title=pubmed['title'], abstract=pubmed['abstract'], pmid=' 12345678 ')])
    client = SimpleNamespace(search_works=lambda *args, **kwargs: [OpenAlexSearchHit(
        title=openalex['title'], abstract=openalex['abstract'], openalex_id='W1234567890')])

    candidates = [federation._crossref_channel('public topic', 1, 'synthetic', None)[0],
                  federation._europepmc_channel('public topic', 1, 'synthetic')[0],
                  federation._openalex_channel(client, 'public topic', 1, 'synthetic', semantic=False)[0]]

    assert [{key: candidate.to_dict()[key] for key in row} for candidate, row in zip(candidates, expected)] == expected
    assert all(candidate.url == '' for candidate in candidates)
