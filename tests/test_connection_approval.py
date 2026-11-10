"""Saved membership, changed auth and stale revisions cannot bypass task consent."""
import pytest

from tests.test_connection_task import connection_home, register
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations import llm_models
from zotero_summarizer.services.llm import connections as task, model_list, factory, operational_check
from zotero_summarizer.services._common import write_json_atomic, write_user_config


def forbid_lookup(monkeypatch):
    monkeypatch.setattr(model_list, 'resolve_api_key', lambda *a: pytest.fail('No metadata credential lookup without current exact approval'))
    monkeypatch.setattr(factory, 'get_api_key', lambda *a: pytest.fail('No verification credential lookup without current exact approval'))


def test_saved_but_unapproved_custom_remote_refuses_before_lookup(connection_home, monkeypatch):
    _, runtime = connection_home
    forbid_lookup(monkeypatch)
    provider = runtime.app_state.config.llm_routing.provider_by_name('other')
    with pytest.raises(APIError) as error:
        model_list.list_models_for_provider(provider)
    assert error.value.error == 'provider_approval_required'


def test_none_approval_cannot_authorize_legacy_auth_for_same_destination_and_slot(connection_home, monkeypatch):
    registered = register(task, base_url='https://example.invalid/exact')
    from zotero_summarizer.models.providers import ProviderConfig
    forged = ProviderConfig.model_validate({**registered['provider'], 'auth_mode': 'legacy'})
    forbid_lookup(monkeypatch)
    with pytest.raises(APIError) as error:
        model_list.list_models_for_provider(forged)
    assert error.value.error == 'provider_approval_required'


@pytest.mark.parametrize('changed', ['auth', 'profile', 'config'])
def test_current_task_approval_expires_for_each_common_owner(connection_home, monkeypatch, changed):
    settings, runtime = connection_home
    registered = register(task, base_url='https://example.invalid/exact', auth='existing', authorization='old')
    provider = runtime.app_state.config.llm_routing.provider_by_name(registered['provider']['name'])
    if changed == 'auth':
        write_json_atomic(settings.data_dir / 'setup_credential_revision.json', {'revision': 'changed'})
    elif changed == 'profile':
        write_json_atomic(settings.data_dir / 'research_feed/profile.json', {'schema_version': 1, 'themes': ['Changed']})
    else:
        runtime.app_state.config.output_language = 'Changed'
    forbid_lookup(monkeypatch)
    with pytest.raises(APIError) as error:
        model_list.list_models_for_provider(provider)
    assert error.value.error == 'provider_approval_required'


def test_current_exact_task_approval_is_reused_with_owned_pre_send_check(connection_home, monkeypatch):
    registered = register(task, base_url='https://example.invalid/exact', auth='existing', authorization='old')
    from zotero_summarizer.models.providers import ProviderConfig
    provider = ProviderConfig.model_validate(registered['provider'])
    seen = []
    monkeypatch.setattr(model_list, 'resolve_api_key', lambda p: seen.append(('key', p.name)) or 'synthetic')
    def list_owned(base, key, **options):
        assert options['owned'] is True
        options['before_send'](None)
        seen.append(('catalog', base, key))
        return ['served']
    monkeypatch.setattr(llm_models, 'list_openai_models', list_owned)
    assert model_list.list_models_for_provider(provider) == ['served']
    assert seen == [('key', provider.name), ('catalog', provider.base_url, 'synthetic')]


def test_policy_refusal_is_unknown_needs_approval_not_endpoint_or_model_failure(connection_home, monkeypatch):
    from zotero_summarizer.services.setup import doctor, validate
    from zotero_summarizer.models.providers import StageModelConfig
    settings, runtime = connection_home
    config = runtime.app_state.config
    config.llm_routing.default.provider = 'other'
    for stage in ('feed', 'backlog', 'deep_review'):
        setattr(config.llm_routing, stage, StageModelConfig())
    write_user_config(settings.config_path, config)
    forbid_lookup(monkeypatch)
    row = operational_check._reach_stage(config.llm_routing, 'deep_review')
    assert row['reachable'] is None and row['status'] == 'needs_approval'
    connection = validate._probe_connection(config)
    assert connection.status == 'needs_approval' and connection.listed is None
    inventory = doctor._runtime_model(settings)
    assert inventory['reason_code'] == 'provider_approval_required'
    assert inventory['recovery']['action'] == 'open_ai_settings'
    assert inventory['recovery'].get('command') is None


def test_legacy_inference_key_resolution_remains_independent(connection_home, monkeypatch):
    _, runtime = connection_home
    provider = runtime.app_state.config.llm_routing.provider_by_name('other')
    monkeypatch.setattr(factory, 'get_api_key', lambda slot: ('legacy', 'environment'))
    assert factory.resolve_api_key(provider) == 'legacy'
