"""Adversarial persistence, shared consumers, bootstrap and revision boundaries."""
from uuid import uuid4

import pytest

from tests.test_connection_task import connection_home, register
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.connections import ConnectionSelection, ReplaceConnectionCredential
from zotero_summarizer.services.llm import connections as task, connection_state as store
from zotero_summarizer.services._common import write_json_atomic


def selection(registered, model='manual'):
    return ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'], model=model,
                               corrections={} if registered.get('bootstrap') else {'feed': 'previous', 'backlog': 'previous'})


def test_crossed_routes_require_a_real_choice_and_previous_is_an_explicit_correction(connection_home):
    from zotero_summarizer.models.providers import resolve_stage
    _, runtime = connection_home
    before = runtime.app_state.config.llm_routing.model_copy(deep=True)
    registered = register(task)
    command = selection(registered).model_copy(update={'corrections': {}})
    preview = task.preview(command)
    assert preview['ready_to_apply'] is False
    assert preview['impact']['required_corrections'] == ['feed', 'backlog']
    with pytest.raises(APIError):
        task.apply(command.model_copy(update={'preview': preview['preview']}))
    command = command.model_copy(update={'corrections': {'feed': 'previous', 'backlog': 'default'}})
    preview = task.preview(command)
    assert preview['ready_to_apply'] is True
    task.apply(command.model_copy(update={'preview': preview['preview']}))
    after = runtime.app_state.config.llm_routing
    assert resolve_stage(after, 'feed').provider.name == resolve_stage(before, 'feed').provider.name
    assert resolve_stage(after, 'feed').model == resolve_stage(before, 'feed').model
    assert after.feed.provider == 'old' and after.feed.model == 'feed-model'
    assert after.backlog.provider is None and after.backlog.model is None


def test_endpoint_change_copies_options_to_inactive_candidate(connection_home):
    _, runtime = connection_home
    old = runtime.app_state.config.llm_routing.provider_by_name('old')
    old.extra_body = {'reasoning_effort': 'low'}
    before = old.model_dump(mode='json')
    registered = register(task, source='old', base_url='https://example.invalid/exact/proxy')
    assert registered['provider']['extra_body'] == before['extra_body']
    assert runtime.app_state.config.llm_routing.provider_by_name('old').model_dump(mode='json') == before
    assert runtime.app_state.config.llm_routing.default.provider == 'old'
    assert registered['provider']['api_key_env'] != old.api_key_env


def test_native_null_base_and_unavailable_catalog_keep_manual_path(connection_home, monkeypatch):
    from zotero_summarizer.integrations import llm_models
    monkeypatch.setattr(llm_models, 'list_anthropic_models', lambda *a, **kw: (_ for _ in ()).throw(llm_models.ModelListError('unsupported catalog')))
    registered = register(task, type='anthropic', base_url=None)
    assert registered['provider']['type'] == 'anthropic' and registered['provider']['base_url'] is None
    catalog = task.catalog(registered['operation_id'])
    assert catalog['catalog'] == 'unavailable'
    command = selection(registered)
    preview = task.preview(command)
    assert task.apply(command.model_copy(update={'preview': preview['preview']}))['state'] == 'selection_saved'


@pytest.mark.parametrize('changed', ['auth', 'profile', 'config'])
def test_each_server_revision_retires_catalog_and_preview(connection_home, changed):
    settings, runtime = connection_home
    registered = register(task)
    command = selection(registered)
    preview = task.preview(command)
    if changed == 'auth':
        write_json_atomic(settings.data_dir / 'setup_credential_revision.json', {'revision': 'replacement'})
    elif changed == 'profile':
        write_json_atomic(settings.data_dir / 'research_feed/profile.json', {'schema_version': 1, 'themes': ['different']})
    else:
        runtime.app_state.config.output_language = 'Changed'
    for action in (lambda: task.catalog(command.operation_id), lambda: task.apply(command.model_copy(update={'preview': preview['preview']}))):
        with pytest.raises(APIError) as error:
            action()
        assert error.value.status_code == 409
    assert runtime.app_state.config.llm_routing.default.model == 'old-model'


def test_shared_slot_and_separate_strict_telegram_route_are_in_impact(connection_home):
    settings, runtime = connection_home
    write_json_atomic(settings.data_dir / 'research_feed/profile.json', {
        'schema_version': 1, 'themes': ['Synthetic'], 'projects': [],
        'telegram_model': {'provider': 'old', 'model': 'telegram-model'},
    })
    registered = register(task, source='old', auth='existing', authorization='old')
    preview = task.preview(selection(registered))['impact']
    assert preview['shared_authorization']['providers'] == ['old', 'other', registered['provider']['name']]
    assert set(preview['shared_authorization']['consumers']) == {'default', 'feed', 'backlog', 'deep_review', 'telegram'}
    assert preview['telegram']['selection'] == {'provider': 'old', 'model': 'telegram-model'}
    assert preview['telegram']['admitted'] is True
    assert 'telegram' in preview['existing_server']['consumers']
    runtime.app_state.config.llm_routing.provider_by_name('old').base_url = 'http://machine.localhost:9999/v1'
    registered = register(task, source='old')
    assert task.preview(selection(registered))['impact']['telegram']['admitted'] is False


def test_lost_registration_ack_reconciles_exact_persisted_provider_without_rewrite(connection_home, monkeypatch):
    save = store.save
    calls = []
    def lose_ack(receipt):
        calls.append(receipt['state'])
        if receipt['state'] == 'registered_inactive':
            raise OSError('Lost final receipt write')
        return save(receipt)
    monkeypatch.setattr(store, 'save', lose_ack)
    operation = str(uuid4())
    with pytest.raises(OSError):
        register(task, operation_id=operation)
    result = task.readback(operation)
    assert result['provider_matches'] is True and result['state'] == 'registered_inactive'
    assert result['selection_matches'] is False
    assert calls == ['draft', 'registered_inactive']


def test_lost_selection_ack_reads_actual_default_without_replaying(connection_home, monkeypatch):
    registered = register(task)
    command = selection(registered)
    preview = task.preview(command)
    def lose_ack(receipt):
        raise OSError('Lost selection acknowledgement')
    monkeypatch.setattr(store, 'save', lose_ack)
    with pytest.raises(OSError):
        task.apply(command.model_copy(update={'preview': preview['preview']}))
    result = task.readback(command.operation_id)
    assert result['selection_matches'] is True and result['state'] == 'selection_saved'


@pytest.mark.parametrize('base', ['https://example.invalid/exact', 'http://[::1]:9999/exact'])
def test_bootstrap_has_no_placeholder_model_or_unapproved_remote_discovery(connection_home, monkeypatch, base):
    from zotero_summarizer.integrations import llm_models
    settings, runtime = connection_home
    settings.config_path.unlink()
    runtime.app_state = None
    seen = []
    monkeypatch.setattr(llm_models, 'list_openai_models', lambda *a, **kw: seen.append(a) or ['served'])
    registered = register(task, base_url=base)
    assert registered['bootstrap'] and not settings.config_path.exists()
    task.catalog(registered['operation_id'])
    assert bool(seen) == base.startswith('http:')
    command = selection(registered, 'explicit-first-model')
    preview = task.preview(command)
    task.apply(command.model_copy(update={'preview': preview['preview']}))
    assert runtime.app_state.config.llm_routing.default.model == 'explicit-first-model'
    assert task.readback(command.operation_id)['selection_matches'] is True


def test_credential_replacement_requires_shared_preview_and_blocks_started_telegram(connection_home, monkeypatch):
    from zotero_summarizer.services.llm import connection_credentials, credentials
    from zotero_summarizer.services.research_feed.telegram_workflow import _exclusive
    settings, _ = connection_home
    writes = []
    monkeypatch.setattr(credentials.keyring, 'set_password', lambda *a: writes.append(a))
    command = ReplaceConnectionCredential(provider='old', revision=store.revision())
    preview = connection_credentials.replace(command)
    assert set(preview['impact']['providers']) == {'old', 'other'}
    with _exclusive(settings), pytest.raises(APIError) as error:
        connection_credentials.replace(command.model_copy(update={'preview': preview['preview'], 'secret': 'synthetic-replacement'}))
    assert error.value.error == 'telegram_busy' and writes == []
    command = ReplaceConnectionCredential(provider='old', revision=store.revision())
    preview = connection_credentials.replace(command)
    response = connection_credentials.replace(command.model_copy(update={'preview': preview['preview'], 'secret': 'synthetic-replacement'}))
    assert response['state'] == 'auth_stored' and len(writes) == 1
    assert connection_credentials.readback(preview['preview'])['state'] == 'auth_stored'
    with pytest.raises(APIError):
        store.require_revision(command.revision)
