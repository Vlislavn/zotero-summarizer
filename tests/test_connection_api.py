"""HTTP revision, explicit consent and config-scoped merge boundaries."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_connection_task import connection_home
from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import config, llm, setup


@pytest.fixture
def connection_api(connection_home):
    app = FastAPI()
    app.include_router(config.router)
    app.include_router(llm.router)
    app.include_router(setup.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        yield client


def test_http_register_catalog_impact_apply_readback_and_full_write_conflict(connection_api, monkeypatch):
    from zotero_summarizer.integrations import llm_models
    monkeypatch.setattr(llm_models, 'list_openai_models', lambda *a, **kw: ['served'])
    client = connection_api
    stale = client.get('/api/config').json()
    revision = client.get('/api/llm/connections').json()['revision']
    command = {'operation_id': str(uuid4()), 'revision': revision, 'type': 'openai',
               'base_url': 'https://example.invalid/exact/proxy', 'auth': 'none', 'approved': True}
    registered = client.post('/api/llm/connections', json=command)
    assert registered.status_code == 200
    result = registered.json()
    assert client.get('/api/config').json()['llm_routing']['default'] == stale['llm_routing']['default']
    assert client.put('/api/config', json=stale).status_code == 409
    assert client.put('/api/config', json={k: v for k, v in stale.items() if k != '_revision'}).status_code == 428
    catalog = client.post(f"/api/llm/connections/{command['operation_id']}/catalog").json()
    selection = {'operation_id': command['operation_id'], 'revision': catalog['revision'], 'model': 'served'}
    impact = client.post('/api/llm/connections/impact', json=selection)
    assert impact.status_code == 200 and impact.json()['ready_to_apply'] is False
    assert client.post('/api/llm/connections/apply', json={**selection, 'preview': impact.json()['preview']}).status_code == 409
    rejected = {**selection, 'corrections': {'feed': 'keep', 'backlog': 'keep'}}
    assert client.post('/api/llm/connections/impact', json=rejected).status_code == 422
    assert client.post('/api/llm/connections/apply', json={**rejected, 'preview': impact.json()['preview']}).status_code == 422
    selection['corrections'] = {'feed': 'previous', 'backlog': 'previous'}
    impact = client.post('/api/llm/connections/impact', json=selection)
    assert impact.json()['ready_to_apply'] is True
    applied = client.post('/api/llm/connections/apply', json={**selection, 'preview': impact.json()['preview']})
    assert applied.status_code == 200 and applied.json()['selection_matches'] is True
    readback = client.get(f"/api/llm/connections/{command['operation_id']}").json()
    assert readback['provider_matches'] and readback['selection_matches']
    assert readback['provider']['name'] == result['provider']['name']
    assert client.post('/api/llm/connections/apply', json={**selection, 'preview': impact.json()['preview']}).status_code == 409


def test_consent_must_be_literal_and_precede_destination_secret_lookup(connection_api, monkeypatch):
    from zotero_summarizer.services.llm import factory
    monkeypatch.setattr(factory, 'get_api_key', lambda *a: pytest.fail('no credential lookup before consent'))
    command = {'operation_id': str(uuid4()), 'revision': connection_api.get('/api/llm/connections').json()['revision'],
               'base_url': 'https://example.invalid/changed', 'auth': 'existing', 'authorization': 'old', 'approved': False}
    assert connection_api.post('/api/llm/connections', json=command).status_code == 403
    assert connection_api.post('/api/llm/connections', json={**command, 'approved': 'true'}).status_code == 422
    assert connection_api.get('/api/llm/connections/not-a-uuid').status_code == 422


def test_legacy_credential_revision_is_bound_and_does_not_bypass_shared_preview(connection_api, connection_home, monkeypatch):
    from zotero_summarizer.services.llm import credentials
    _, runtime = connection_home
    writes = []
    monkeypatch.setattr(credentials.keyring, 'set_password', lambda *args: writes.append(args))
    revision = connection_api.get('/api/llm/connections').json()['revision']
    command = {'name': 'SHARED_KEY', 'api_key': 'synthetic', 'revision': revision}
    assert connection_api.put('/api/setup/ai-credential', json=command).status_code == 409
    assert writes == []
    runtime.app_state.config.llm_routing.provider_by_name('other').api_key_env = 'OTHER_KEY'
    command['revision'] = connection_api.get('/api/llm/connections').json()['revision']
    response = connection_api.put('/api/setup/ai-credential', json=command)
    assert response.status_code == 200
    assert response.json()['revision'] == connection_api.get('/api/config').json()['_revision']
    assert response.json()['revision'] != command['revision'] and len(writes) == 1
    assert connection_api.put('/api/setup/ai-credential', json=command).status_code == 409
    assert len(writes) == 1


def test_separate_saved_model_test_refuses_unknown_local_load_before_any_client(connection_api, monkeypatch):
    monkeypatch.setattr('zotero_summarizer.integrations.ollama_inventory._models', lambda *_a: None)  # Ollama size unknown: no network, no memory reading
    from zotero_summarizer.services.llm import operational_check, factory
    monkeypatch.setattr(factory, 'get_api_key', lambda *a: pytest.fail('local UNKNOWN must precede credentials'))
    monkeypatch.setattr(operational_check, 'build_client_for_provider', lambda *a, **kw: pytest.fail('No local client construction'))
    from zotero_summarizer.services.llm import local_test_reachability
    monkeypatch.setattr(local_test_reachability.model_list, 'list_models_for_provider', lambda provider: [])  # metadata only
    saved = connection_api.get('/api/config').json()
    response = connection_api.post('/api/llm/connections/test', json={'revision': saved['_revision'], 'model': saved['llm_routing']['default']['model']})
    assert response.status_code == 200
    result = response.json()
    assert result['attempted'] is False and result['hardware_safe'] == 'unknown' and result['reachable'] is True
    assert result['status'] != 'operational' and result.get('response') is None
    assert connection_api.get('/api/config').json()['llm_routing'] == saved['llm_routing']


def test_no_auth_status_does_not_claim_or_resolve_a_key(connection_home, monkeypatch):
    from zotero_summarizer.services.setup import status
    from zotero_summarizer.services.llm import credentials
    _, runtime = connection_home
    for provider in runtime.app_state.config.llm_routing.providers:
        provider.auth_mode = 'none'
    monkeypatch.setattr(credentials, 'get_api_key', lambda *a: pytest.fail('no-auth status must not resolve keys'))
    async def reachability():
        return {'stages': []}
    monkeypatch.setattr(status.operational_check, 'check_reachability', reachability)
    result = asyncio.run(status._llm_status(runtime.app_state.config))
    assert result.auth_required is False and result.api_key_present is False
