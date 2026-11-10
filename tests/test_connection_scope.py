"""Explicit model assignments and probes retain their selected task boundary."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.test_connection_task import connection_home, register
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.connections import ConnectionSelection
from zotero_summarizer.services.llm import connections, connection_impact


def test_named_tasks_change_without_replacing_default_or_other_tasks(connection_home):
    _, runtime = connection_home
    before = runtime.app_state.config.model_copy(deep=True)
    registered = register(connections)
    choice = ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'],
                                 model='chosen', targets=['feed', 'deep_review'])
    preview = connections.preview(choice)
    assert preview['ready_to_apply'] is True
    assert preview['impact']['targets'] == ['feed', 'deep_review']
    assert preview['impact']['required_corrections'] == []
    assert runtime.app_state.config.llm_routing.default == before.llm_routing.default
    saved = connections.apply(choice.model_copy(update={'preview': preview['preview']}))
    actual = runtime.app_state.config.llm_routing
    assert actual.default == before.llm_routing.default
    assert actual.backlog == before.llm_routing.backlog
    for stage in choice.targets:
        assert getattr(actual, stage).model_dump() == {'provider': saved['provider']['name'], 'model': 'chosen'}
    for provider in before.llm_routing.providers:
        assert actual.provider_by_name(provider.name) == provider
    assert runtime.app_state.config.research_goals == before.research_goals
    assert connections.readback(registered['operation_id'])['selection_matches'] is True


def test_default_and_named_task_still_require_other_partial_pair_corrections(connection_home):
    registered = register(connections)
    choice = ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'],
                                 model='chosen', targets=['default', 'deep_review'])
    preview = connections.preview(choice)
    assert set(preview['impact']['required_corrections']) == {'feed', 'backlog'}
    assert preview['ready_to_apply'] is False
    fixed = choice.model_copy(update={'corrections': {'feed': 'previous', 'backlog': 'previous'}})
    preview = connections.preview(fixed)
    saved = connections.apply(fixed.model_copy(update={'preview': preview['preview']}))
    assert saved['config']['llm_routing']['default']['model'] == 'chosen'
    assert saved['config']['llm_routing']['deep_review']['model'] == 'chosen'
    assert saved['config']['llm_routing']['feed']['model'] == 'feed-model'


def test_preview_cannot_be_reused_for_a_different_assignment_scope(connection_home):
    registered = register(connections)
    choice = ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'],
                                 model='chosen', targets=['deep_review'])
    preview = connections.preview(choice)
    with pytest.raises(APIError) as error:
        connections.apply(choice.model_copy(update={'preview': preview['preview'], 'targets': ['feed']}))
    assert error.value.status_code == 409


@pytest.mark.parametrize('targets', [[], ['feed', 'feed'], ['telegram'], ['unknown']])
def test_assignment_scope_is_closed_nonempty_and_unambiguous(targets):
    with pytest.raises(ValidationError):
        ConnectionSelection(operation_id=uuid4(), revision='r', model='m', targets=targets)


def test_impact_remaining_consumers_are_taken_from_after_configuration(connection_home):
    _, runtime = connection_home
    before = runtime.app_state.config
    after = before.model_copy(deep=True)
    after.llm_routing.default.provider = 'other'
    after.llm_routing.feed.provider = 'other'
    from zotero_summarizer.models.research_feed import ResearchProfile
    result = connection_impact.impact(before, after, ResearchProfile(themes=['Synthetic'], projects=[]),
                                      after.llm_routing.provider_by_name('other'), source='old')
    assert result['existing_server']['consumers'] == ['deep_review']


def test_task_probe_deduplicates_only_selected_saved_pairs(connection_home, monkeypatch):
    from zotero_summarizer.models.connections import TaskModelsTestRequest
    from zotero_summarizer.services.llm import connection_test, operational_check
    _, runtime = connection_home
    runtime.app_state.config.llm_routing.feed.model = 'review-model'
    seen = []
    def probe(provider, model, **_kw):
        seen.append((provider.name, model))
        return {'status': 'fail', 'attempted': False, 'reason_code': 'synthetic_refusal', 'detail': 'Not submitted'}
    monkeypatch.setattr(operational_check, 'probe_provider', probe)
    result = connection_test.test_tasks(TaskModelsTestRequest(revision=connections.context()['revision'],
                                                             stages=['feed', 'deep_review']))
    assert seen == [('old', 'review-model')]
    assert [row['stage'] for row in result['stages']] == ['feed', 'deep_review']
    assert result['status'] == 'degraded' and result['current'] is True
    assert result['targets'] == ['feed', 'deep_review']


def test_local_task_probe_stays_unknown_without_constructing_a_client(connection_home, monkeypatch):
    monkeypatch.setattr('zotero_summarizer.integrations.ollama_inventory._models', lambda *_a: None)  # Ollama size unknown: no network, no memory reading
    from zotero_summarizer.models.connections import TaskModelsTestRequest
    from zotero_summarizer.services.llm import connection_test, operational_check
    monkeypatch.setattr(operational_check, 'build_client_for_provider', lambda *a, **kw: pytest.fail('No local load'))
    from zotero_summarizer.services.llm import local_test_reachability
    monkeypatch.setattr(local_test_reachability.model_list, 'list_models_for_provider', lambda provider: [])  # metadata only
    result = connection_test.test_tasks(TaskModelsTestRequest(revision=connections.context()['revision'], stages=['deep_review']))
    assert result['status'] == 'degraded'
    assert result['stages'][0]['attempted'] is False
    assert result['stages'][0]['hardware_safe'] == 'unknown'
    assert result['stages'][0]['reason_code'] == 'model_size_unknown'


def test_task_probe_requires_loaded_saved_assignments(connection_home):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.models.connections import TaskModelsTestRequest
    from zotero_summarizer.services.llm import connection_test
    _, runtime = connection_home
    runtime.app_state = None
    with pytest.raises(APIError) as raised:
        connection_test.test_tasks(TaskModelsTestRequest(revision=connections.context()['revision'], stages=['deep_review']))
    assert raised.value.error == 'selection_required' and raised.value.status_code == 409


def test_task_probe_http_scope_is_validated_before_model_work(connection_home, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zotero_summarizer.api.routes import llm
    from zotero_summarizer.services.llm import operational_check
    app = FastAPI()
    app.include_router(llm.router)
    monkeypatch.setattr(operational_check, 'build_client_for_provider', lambda *a, **kw: pytest.fail('No local client'))
    with TestClient(app) as client:
        revision = connections.context()['revision']
        response = client.post('/api/llm/connections/test-tasks', json={'revision': revision, 'stages': ['deep_review']})
        assert response.status_code == 200 and response.json()['stages'][0]['attempted'] is False
        for stages in ([], ['telegram'], ['feed', 'feed']):
            assert client.post('/api/llm/connections/test-tasks', json={'revision': revision, 'stages': stages}).status_code == 422


def test_task_probe_ai_off_and_stale_revision_never_submit(connection_home, monkeypatch):
    from zotero_summarizer.models.connections import TaskModelsTestRequest
    from zotero_summarizer.services.llm import connection_test, operational_check
    _, runtime = connection_home
    old_revision = connections.context()['revision']
    runtime.app_state.config.llm_enabled = False
    monkeypatch.setattr(operational_check, 'probe_provider', lambda *a: pytest.fail('No probe'))
    with pytest.raises(APIError):
        connection_test.test_tasks(TaskModelsTestRequest(revision=old_revision, stages=['feed']))
    result = connection_test.test_tasks(TaskModelsTestRequest(revision=connections.context()['revision'], stages=['feed']))
    assert result['status'] == 'disabled' and result['attempted'] is False
    assert [row['stage'] for row in result['stages']] == ['feed']
