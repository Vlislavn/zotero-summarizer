"""Connection task acceptance: owned config, no credentials/network/models."""
import asyncio
from uuid import uuid4

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import AppState, GoalsConfig
from zotero_summarizer.runtime import AppContext, RuntimeState, set_context
from zotero_summarizer.settings import Settings
from zotero_summarizer.services._common import write_user_config


@pytest.fixture
def connection_home(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    config = GoalsConfig.model_validate({
        'research_goals': ['Saved research'], 'triage_criteria': ['Evidence'], 'summary_structure': ['Summary'],
        'corpus': {'enabled': False},
        'relevance_scale': {1: 'a', 2: 'b', 3: 'c', 4: 'd', 5: 'e'},
        'llm': {'draft_model': 'old-model', 'refine_model': 'old-model', 'api_base': 'http://127.0.0.1:9999/proxy/v1', 'api_key_env': 'SHARED_KEY'},
        'llm_routing': {
            'providers': [
                {'name': 'old', 'base_url': 'http://127.0.0.1:9999/proxy/v1', 'api_key_env': 'SHARED_KEY'},
                {'name': 'other', 'base_url': 'https://example.invalid/native', 'api_key_env': 'SHARED_KEY'},
            ],
            'default': {'provider': 'old', 'model': 'old-model'},
            'feed': {'model': 'feed-model'}, 'backlog': {'provider': 'other'},
            'deep_review': {'provider': 'old', 'model': 'review-model'},
        },
    })
    write_user_config(settings.config_path, config)
    runtime = RuntimeState(app_state=AppState(config=config))
    set_context(AppContext(settings, runtime))
    return settings, runtime


def register(task, **fields):
    from zotero_summarizer.models.connections import RegisterConnection
    return task.register(RegisterConnection.model_validate({
        'operation_id': str(uuid4()), 'revision': task.context()['revision'],
        'type': 'openai', 'base_url': 'http://[::1]:9988/exact/proxy',
        'auth': 'none', 'approved': True, **fields,
    }))


def test_register_then_explicit_apply_preserves_previous_complete_pairs(connection_home, monkeypatch):
    from zotero_summarizer.services.llm import connections as task
    from zotero_summarizer.models.connections import ConnectionSelection
    from zotero_summarizer.integrations import llm_models
    monkeypatch.setattr(llm_models, 'list_openai_models', lambda *a, **kw: ['new-model'])
    _, runtime = connection_home
    before = runtime.app_state.config.model_dump(mode='json')
    result = register(task)
    assert result['state'] == 'registered_inactive'
    assert runtime.app_state.config.llm_routing.default.model == 'old-model'
    assert runtime.app_state.config.research_goals == ['Saved research']
    catalog = task.catalog(result['operation_id'])
    assert catalog['models'] == ['new-model']
    selection = ConnectionSelection(operation_id=result['operation_id'], model='new-model', revision=catalog['revision'], corrections={'feed': 'previous', 'backlog': 'previous'})
    preview = task.preview(selection)
    assert [(r['stage'], r['after']['model']) for r in preview['impact']['stages']] == [
        ('feed', 'feed-model'), ('backlog', 'old-model'), ('deep_review', 'review-model')]
    assert preview['impact']['stages'][1]['after']['provider'] == 'other'
    assert runtime.app_state.config.llm_routing.default.model == 'old-model'
    saved = task.apply(selection.model_copy(update={'preview': preview['preview']}))
    assert saved['state'] == 'selection_saved'
    after = runtime.app_state.config.model_dump(mode='json')
    from zotero_summarizer.models.providers import resolve_stage
    previous = GoalsConfig.model_validate(before).llm_routing
    for stage in ('feed', 'backlog', 'deep_review'):
        old = resolve_stage(previous, stage)
        saved_pair = resolve_stage(runtime.app_state.config.llm_routing, stage)
        assert (saved_pair.provider.name, saved_pair.model) == (old.provider.name, old.model)
    assert after['llm_routing']['deep_review'] == before['llm_routing']['deep_review']
    assert task.readback(result['operation_id'])['selection_matches'] is True


def test_remote_secret_requires_exact_approval_before_lookup(connection_home, monkeypatch):
    from zotero_summarizer.services.llm import connections as task
    from zotero_summarizer.services.llm import credentials
    monkeypatch.setattr(credentials, 'get_api_key', lambda *a: pytest.fail('no secret lookup'))
    with pytest.raises(APIError) as error:
        register(task, base_url='https://example.invalid/exact', auth='existing', authorization='old', approved=False)
    assert error.value.status_code == 403


def test_no_auth_remote_catalog_never_resolves_secret(connection_home, monkeypatch):
    from zotero_summarizer.services.llm import connections as task, credentials
    from zotero_summarizer.integrations import llm_models
    monkeypatch.setattr(credentials, 'get_api_key', lambda *a: pytest.fail('explicit none lookup'))
    seen = []
    monkeypatch.setattr(llm_models, 'list_openai_models', lambda url, key, **kw: seen.append((url, key)) or [])
    result = register(task, base_url='https://example.invalid/exact/proxy')
    assert task.catalog(result['operation_id'])['catalog'] == 'returned'
    assert seen == [('https://example.invalid/exact/proxy', None)]


def test_changed_config_auth_or_profile_retires_decisions(connection_home):
    from zotero_summarizer.services.llm import connections as task
    from zotero_summarizer.models.connections import ConnectionSelection
    settings, runtime = connection_home
    registered = register(task)
    selection = ConnectionSelection(operation_id=registered['operation_id'], model='manual', revision=registered['revision'], corrections={'feed': 'previous', 'backlog': 'previous'})
    preview = task.preview(selection)
    runtime.app_state.config.output_language = 'French'
    with pytest.raises(APIError) as error:
        task.apply(selection.model_copy(update={'preview': preview['preview']}))
    assert error.value.status_code == 409
    assert runtime.app_state.config.llm_routing.default.model == 'old-model'
    assert task.readback(registered['operation_id'])['provider_matches'] is True
    (settings.data_dir / 'setup_credential_revision.json').write_text('{"revision":"changed"}')
    with pytest.raises(APIError):
        task.catalog(registered['operation_id'])


def test_legacy_full_write_cannot_overwrite_connection_commit(connection_home):
    from zotero_summarizer.services import config
    from zotero_summarizer.services.llm import connections as task
    stale = asyncio.run(config.get_runtime_config())
    register(task)
    with pytest.raises(APIError) as error:
        asyncio.run(config.update_runtime_config(GoalsConfig.model_validate(stale), expected_revision=stale['_revision']))
    assert error.value.status_code == 409


def test_explicit_none_resolver_is_not_locality_or_fake_key(monkeypatch):
    from zotero_summarizer.models.providers import ProviderConfig
    from zotero_summarizer.services.llm import factory
    monkeypatch.setattr(factory, 'get_api_key', lambda *a: pytest.fail('no lookup'))
    provider = ProviderConfig(name='none', base_url='https://example.invalid/proxy', api_key_env='UNUSED', auth_mode='none')
    assert factory.resolve_api_key(provider) is None
