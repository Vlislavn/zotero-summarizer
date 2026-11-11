"""Private stored Related context is not a generic Search wire capability."""
from hashlib import sha256
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tests import test_related_saved_papers as related_cases
from zotero_summarizer.api.routes import library, search
from zotero_summarizer.services.library import related, _related_assessment as semantic
from zotero_summarizer.services.search import session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, SearchIntent
from zotero_summarizer.storage import repositories

journey = related_cases.journey


def _client():
    app = FastAPI()
    app.include_router(library.router)
    app.include_router(search.router)
    return TestClient(app)


def _files(root):
    return {path.name: sha256(path.read_bytes()).hexdigest() for path in root.glob('*') if path.is_file()}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.parametrize('change', ['unchanged', 'seed', 'delete', 'native', 'model', 'unavailable'])
def test_generic_get_and_default_serializer_keep_related_private(journey, monkeypatch, change):
    with _client() as client:
        response = client.post('/api/library/related/SEED0001/assess',
                               json={'consent_token': semantic.model_boundary()['token']})
        assert response.status_code == 200
        result = response.json()
        assert result['assessment_status'] == 'assessed'
        retained = session.load(result['session_id'])
        stored = retained.to_dict(for_storage=True)
        snapshot = retained.related['snapshot']
        private = {record['text'] for paper in [snapshot['seed'], *snapshot['pool']]
                   for record in paper['comments']['records']}
        assert private
        if change == 'seed':
            repositories.upsert_review_note(journey.db, 'feed:42', 'Updated same-count private seed fixture.')
        elif change == 'delete':
            with sqlite3.connect(journey.db) as connection:
                connection.execute("DELETE FROM review_notes WHERE item_key='GOOD0001'")
        elif change == 'native':
            journey.reader.rows['GOOD0001']['notes'] = [{'note_key': 'NATIVE01', 'note': '<p>Changed private note.</p>'}]
        elif change == 'model':
            journey.config.llm_routing.default.model = 'different-model'
        elif change == 'unavailable':
            journey.reader.fail = 'SEED0001'
        before = _files(journey.paths.search_dir)
        database_before = sha256(journey.db.read_bytes()).hexdigest()
        scopes_before = list(journey.reader.scopes)

        def forbidden(*args, **kwargs):
            pytest.fail('Generic projection must not acquire context, credentials or model work')

        monkeypatch.setattr(related, '_snapshot', forbidden)
        monkeypatch.setattr(semantic.factory, 'resolve_stage_client', forbidden)
        read = client.get('/api/search/' + retained.id)
        assert read.status_code == 200
        payload = read.json()
        assert len(journey.model.payloads) == 1
        assert journey.reader.scopes == scopes_before
        assert _files(journey.paths.search_dir) == before
        assert sha256(journey.db.read_bytes()).hexdigest() == database_before
        assert session.load(retained.id).related == retained.related
        assert retained.to_dict(for_storage=True) == stored
        assert payload['id'] == retained.id and payload['status'] == 'related_complete'
        assert payload['related'] == {}
        assert private.isdisjoint(_strings(payload))
        assert retained.to_dict()['related'] == {}
        assert retained.to_dict(for_display=True)['related'] == {}


def test_generic_get_preserves_failed_retry_history_and_explicit_related_replay(journey):
    token = semantic.model_boundary()['token']
    journey.model.mutate = lambda rows: rows.pop()
    failed = related.assess_related('SEED0001', consent_token=token)
    assert failed['assessment_status'] == 'failed'
    journey.model.mutate = None
    completed = related.assess_related('SEED0001', consent_token=token, retry=True)
    assert completed['assessment_status'] == 'assessed'
    retained = session.load(completed['session_id'])
    assert retained.related['prior_attempts'][0]['result'] == failed
    stored = retained.to_dict(for_storage=True)
    before = _files(journey.paths.search_dir)
    database_before = sha256(journey.db.read_bytes()).hexdigest()
    with _client() as client:
        response = client.get('/api/search/' + retained.id)
    assert response.status_code == 200
    assert len(journey.model.payloads) == 2
    assert session.load(retained.id).related == retained.related
    assert retained.to_dict(for_storage=True) == stored
    assert _files(journey.paths.search_dir) == before
    assert sha256(journey.db.read_bytes()).hexdigest() == database_before
    assert response.json()['related'] == {}
    assert related.assess_related('SEED0001', consent_token=token) == completed
    assert len(journey.model.payloads) == 2
    assert _files(journey.paths.search_dir) == before


def test_ordinary_search_get_keeps_metadata_and_ack_without_related_or_work(journey):
    current = session.new_session(raw_query='Owned ordinary query 👀',
                                  intent=SearchIntent(raw_query='Owned ordinary query 👀'),
                                  plan=QueryPlan(), questions=[])
    current.candidates = [Candidate(title='Public 👀 metadata', abstract='Immutable metadata fixture',
                                    doi='10.1/public', candidate_id='PUBLIC01',
                                    in_library=True, existing_zotero_key='SAVED001',
                                    materialized_zotero_key='SAVED001')]
    session.save(current)
    stored = current.to_dict(for_storage=True)
    before = _files(journey.paths.search_dir)
    with _client() as client:
        response = client.get('/api/search/' + current.id)
    assert response.status_code == 200
    payload = response.json()
    assert payload['id'] == current.id and payload['status'] == 'created'
    assert payload['raw_query'] == 'Owned ordinary query 👀'
    assert payload['related'] == {}
    assert len(payload['candidates']) == 1
    row = payload['candidates'][0]
    assert (row['candidate_id'], row['title'], row['abstract'], row['doi']) == (
        'PUBLIC01', 'Public 👀 metadata', 'Immutable metadata fixture', '10.1/public')
    assert row['in_library'] is True
    assert row['existing_zotero_key'] == row['materialized_zotero_key'] == 'SAVED001'
    assert session.load(current.id).to_dict(for_storage=True) == stored
    assert _files(journey.paths.search_dir) == before
    assert journey.model.payloads == [] and journey.reader.scopes == []
