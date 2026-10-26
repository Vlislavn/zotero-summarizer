"""Discovery shares Search work but cannot auto-review or materialize anything."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from tests._zotero_fixtures import add_library_item
from tests.test_discovery_server import journey, begin, PRIVATE
from zotero_summarizer.api.routes import search
from zotero_summarizer.storage import repositories
from zotero_summarizer.services.search import discovery, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, SearchIntent


def test_http_discovery_is_explicit_persisted_and_does_not_start_review(journey, monkeypatch):
    monkeypatch.setattr(search, '_kickoff_review', lambda *a: pytest.fail('Find started Review'))
    monkeypatch.setattr('zotero_summarizer.integrations.zotero_write.ZoteroWriter.apply_feed_materialization',
                        lambda *a, **kw: pytest.fail('Find started native Add'))
    for key, doi in [('SAVED001', '10.1/saved'), ('OTHER001', '10.1/other')]:
        add_library_item(journey.native_db, item_key=key, title='Saved version', doi=doi)
    journey.controls.candidates = [Candidate(title='Seed', doi='10.1/seed'),
        Candidate(title='Saved alias', doi='10.1/saved'), Candidate(title='Already in Zotero', doi='10.1/other'),
        Candidate(title='New, old publication', doi='10.1/new', year=1995), Candidate(title='Unresolved membership')]
    metadata = journey.client.get('/api/library/related/SEED0001/discover').json()
    assert metadata['admission']['state'] == 'saved'
    assert journey.requests == journey.payloads == []
    response = begin(journey)
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'discovery_ready'
    assert len(journey.requests) == 1 and journey.payloads == []
    assert [row['title'] for row in result['candidates']] == ['New, old publication', 'Unresolved membership']
    assert len(result['discovery']['unresolved_membership']) == 1
    assert all(row['review'] == {} and row['materialized_zotero_key'] == '' for row in result['candidates'])
    saved = session.load(result['id'])
    assert saved.questions == [] and len(journey.controls.candidates) - len(saved.candidates) == 3
    assert discovery.discovery_view(saved) == result


def test_repeat_clicks_share_persisted_work_and_context_changes_invalidate(journey):
    first = begin(journey).json()
    before = session._path(first['id']).read_bytes()
    assert begin(journey).json()['id'] == first['id'] and len(journey.requests) == 1
    repositories.upsert_review_note(journey.paths.triage_db_path, 'feed:42', 'Same-count PRIVATE note edited')
    second = begin(journey).json()
    assert second['id'] != first['id'] and len(journey.requests) == 2
    assert first['discovery']['context_fingerprint'] != second['discovery']['context_fingerprint']
    assert session._path(first['id']).read_bytes() == before


def test_concurrent_clicks_share_one_screen(journey):
    metadata = discovery.discovery_metadata('SEED0001')
    barrier = Barrier(2)

    def click():
        barrier.wait()
        return discovery.find_new('SEED0001', context_fingerprint=metadata['context_fingerprint']).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: click(), range(2)))
    assert len(set(results)) == 1 and len(journey.requests) == 1
    assert journey.payloads == []


def test_legacy_session_wire_does_not_gain_discovery_key(journey):
    sess = session.new_session(raw_query='Topic', questions=[], intent=SearchIntent(raw_query='Topic'), plan=QueryPlan())
    for display in (False, True):
        wire = sess.to_dict(for_display=display)
        assert 'discovery' not in wire
        assert type(sess).from_dict(wire).to_dict(for_display=display) == wire
    assert sess.to_dict(for_storage=True)['discovery'] == {}


def test_public_query_never_uses_private_detail_fields(journey):
    result = begin(journey).json()
    assert result['raw_query'].splitlines() == ['Public seed', 'Public controlled evaluation', 'doi:10.1/seed']
    public = {key: result[key] for key in ('raw_query', 'intent', 'plan', 'candidates')}
    assert PRIVATE not in json.dumps(public, ensure_ascii=False)
    assert PRIVATE not in json.dumps(journey.requests, ensure_ascii=False)
    assert journey.payloads == []


def test_provider_failures_are_persisted_separately_and_explicit_retry_supersedes_snapshot(journey, monkeypatch):
    from zotero_summarizer.integrations import openalex
    from zotero_summarizer.services.search import federate

    monkeypatch.setattr(openalex._RATE_LIMITER, 'acquire', lambda: None)
    requests, retry = [], {'ready': False}

    def respond(request):
        requests.append(request)
        return (httpx.Response(429, text='RAW_PRIVATE_FAILURE')
                if 'search.semantic' in request.url.params and not retry['ready']
                else httpx.Response(200, json={'results': []}))

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as http:
        client = openalex.OpenAlexClient(object(), http_client=http)
        monkeypatch.setattr(discovery, '_search_openalex_client', lambda *a: client)
        monkeypatch.setattr(discovery, 'federate', federate.federate)
        first = begin(journey).json()
        prior = session.load(first['id']).discovery['retrieval']
        assert prior['status'] == 'partial'
        outcomes = [row['outcome'] for row in prior['variants']]
        assert {row['status'] for row in outcomes} == {'success_empty', 'failed'}
        assert all(row['count'] == (None if row['status'] == 'failed' else 0) for row in outcomes)
        count = len(requests)
        assert begin(journey).json()['id'] == first['id'] and len(requests) == count
        retry['ready'] = True
        second = begin(journey, retry=True).json()
        assert second['id'] == first['id'] and len(requests) == count * 2
        assert second['discovery']['retrieval']['status'] == 'observed'
        assert begin(journey).json()['id'] == second['id'] and len(requests) == count * 2
    saved = session.load(second['id'])
    assert saved.discovery['attempts'][0]['retrieval'] == prior
    assert 'RAW_PRIVATE_FAILURE' not in json.dumps(saved.discovery)


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
    module = import_module(f'zotero_summarizer.integrations.{module_name}')
    monkeypatch.setattr(module._RATE_LIMITER, 'acquire', lambda: None)
    response = httpx.Response(status, text=payload) if isinstance(payload, str) else httpx.Response(status, json=payload)
    requested = []

    def transport(request):
        requested.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        call = (module.OpenAlexClient(SimpleNamespace(), http_client=client).search_works
                if provider == 'openalex' else lambda query, **kwargs: getattr(module, function_name)(
                    query, http_client=client, **kwargs))
        receipt = {'status': 'unknown', 'count': None, 'error_code': None}
        assert call('public synthetic topic', outcome=receipt) == []
    assert len(requested) == 1
    assert requested[0].url.host == {'arxiv': 'export.arxiv.org', 'crossref': 'api.crossref.org',
        'europepmc': 'www.ebi.ac.uk', 'semantic_scholar': 'api.semanticscholar.org',
        'openalex': 'api.openalex.org'}[provider]
    assert receipt['status'] == expected
    assert receipt['count'] == (0 if status == 200 else None)
    assert receipt['error_code'] == (None if status == 200 else 'rate_limited')
    assert set(receipt) == {'status', 'count', 'error_code'}
    assert 'public synthetic topic' not in str(receipt)


def test_existing_federation_executor_binds_and_isolates_each_request_outcome(monkeypatch):
    import importlib
    federation = importlib.import_module('zotero_summarizer.services.search.federate')
    barrier = Barrier(2)

    def leaf(query, **kwargs):
        barrier.wait()
        kwargs['outcome'].update(status='success_empty' if query == 'A' else 'failed',
                                 count=0 if query == 'A' else None,
                                 error_code=None if query == 'A' else 'transport_error')
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


def test_outcome_capture_resets_and_rejects_raw_diagnostics(monkeypatch):
    from zotero_summarizer.integrations import arxiv, crossref

    for module in (arxiv, crossref):
        monkeypatch.setattr(module._RATE_LIMITER, 'acquire', lambda: None)
    outer, inner = {}, {}

    def fail(request):
        raise httpx.ConnectError('raw URL or exception https://invalid.example/PRIVATE')

    with httpx.Client(transport=httpx.MockTransport(fail), trust_env=False) as crossref_client:
        def respond(request):
            assert crossref.search_crossref('nested', http_client=crossref_client, outcome=inner) == []
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>')

        with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as arxiv_client:
            assert arxiv.search_arxiv('outer', http_client=arxiv_client, outcome=outer) == []
        assert crossref.search_crossref('ordinary', http_client=crossref_client) == []
    assert inner == {'status': 'failed', 'count': None, 'error_code': 'transport_error'}
    assert outer == {'status': 'success_empty', 'count': 0, 'error_code': None}
    assert 'raw URL or exception' not in json.dumps([outer, inner])
    assert 'PRIVATE' not in json.dumps([outer, inner])


def test_discovery_skips_listed_session_deleted_before_locked_load(journey, monkeypatch):
    first = begin(journey).json()
    fp = first['discovery']['context_fingerprint']
    original_glob, listed = Path.glob, []

    def list_then_delete(parent, pattern, **kwargs):
        paths = list(original_glob(parent, pattern, **kwargs))
        if parent == journey.paths.search_dir and pattern == f'discovery_{fp}_*.json' and not listed:
            assert session._path(first['id']) in paths
            listed.append(first['id'])
            session.delete(first['id'])
        return iter(paths)

    monkeypatch.setattr(Path, 'glob', list_then_delete)
    result = begin(journey).json()
    assert listed == [first['id']] and session.is_deleted(first['id'])
    assert result['id'] != first['id'] and not session._path(first['id']).exists()
    assert discovery.discovery_view(session.load(result['id'])) == result
    assert [row['title'] for row in result['candidates']] == ['Public candidate', 'Unknown identity']
    assert len(journey.requests) == 2 and journey.payloads == []


def test_retry_keeps_replacement_usable_when_prior_deleted_after_replacement_save(journey, monkeypatch):
    first = begin(journey).json()
    repositories.upsert_review_note(journey.paths.triage_db_path, 'feed:42', 'Changed-context PRIVATE note')
    original_save_merge = session.save_merge

    def save_then_delete_prior(replacement):
        saved = original_save_merge(replacement)
        assert session.load(replacement.id).id == replacement.id and replacement.id != first['id']
        session.delete(first['id'])
        return saved

    monkeypatch.setattr(session, 'save_merge', save_then_delete_prior)
    replacement = begin(journey, retry=True).json()
    assert replacement['id'] != first['id'] and session.is_deleted(first['id'])
    with pytest.raises(FileNotFoundError):
        session.load(first['id'])
    assert discovery.discovery_view(session.load(replacement['id'])) == replacement
    assert begin(journey).json()['id'] == replacement['id']
    assert len(journey.requests) == 2 and journey.payloads == []


def test_session_skip_is_tombstone_only_and_default_errors_remain_loud(journey):
    result = begin(journey).json()
    stored, fp = session.load(result['id']), result['discovery']['context_fingerprint']
    session.delete(stored.id)
    mutations = []
    assert session.find_discovery(fp) is None
    for item_key in (stored.id, 'untombstoned-missing'):
        with pytest.raises(FileNotFoundError):
            session.load(item_key)
        with pytest.raises(FileNotFoundError):
            session.update(item_key, mutations.append)
    corrupt = f'discovery_{fp}_' + 'c' * 12
    session._path(corrupt).write_text('{broken', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        session.load(corrupt)
    with pytest.raises(json.JSONDecodeError):
        session.find_discovery(fp)
    with pytest.raises(json.JSONDecodeError):
        session.update(corrupt, mutations.append)
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
