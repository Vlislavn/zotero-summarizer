"""Independent-review discriminators over owned state/HTTP only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest

from tests.test_connection_task import connection_home, register
from tests.test_connection_native_http import native_http
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.connections import ConnectionSelection, RegisterConnection, ReplaceConnectionCredential, ModelTestRequest
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services.llm import connections as task, connection_state as store


def test_model_only_change_reuses_saved_server_and_does_not_cross_unchanged_feed(connection_home):
    _, runtime = connection_home
    before = runtime.app_state.config.model_copy(deep=True)
    registered = register(task, source='old', auth='existing', authorization='old',
                          base_url=before.llm_routing.providers[0].base_url)
    assert registered['provider']['name'] == 'old' and registered['reused'] is True
    assert len(runtime.app_state.config.llm_routing.providers) == len(before.llm_routing.providers)
    command = ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'], model='next')
    preview = task.preview(command)
    assert preview['impact']['required_corrections'] == ['backlog']
    feed = next(row for row in preview['impact']['stages'] if row['stage'] == 'feed')
    assert feed['before'] == feed['after']
    assert resolve_stage(runtime.app_state.config.llm_routing, 'feed') == resolve_stage(before.llm_routing, 'feed')
    assert runtime.app_state.config.llm_routing.default.model == 'old-model'


def test_model_only_custom_remote_approval_uses_fresh_exact_task_receipt(connection_home):
    from zotero_summarizer.services.llm.connection_approval import require_approval
    _, runtime = connection_home
    provider = runtime.app_state.config.llm_routing.provider_by_name('other')
    registered = register(task, source='other', base_url=provider.base_url, auth='existing', authorization='other')
    assert registered['provider']['name'] == 'other' and registered['reused']
    assert require_approval(provider) == registered['revision']
    with pytest.raises(APIError):
        require_approval(provider.model_copy(update={'api_key_env': 'FORGED_SLOT'}))


def test_noauth_edit_transition_preserves_owned_auth_and_actual_native_wire(native_http, connection_home, monkeypatch):
    from zotero_summarizer.services.llm import factory
    _, runtime = connection_home
    original = runtime.app_state.config.llm_routing.provider_by_name('old')
    original.auth_mode = 'none'
    original.extra_body = {'reasoning_effort': 'low'}
    before = original.model_dump(mode='json')
    monkeypatch.setattr(factory, 'get_api_key', lambda *_: pytest.fail('no-auth edit cannot resolve a synthetic old secret'))
    registered = register(task, source='old', type='anthropic', base_url='https://example.invalid/changed', auth='none')
    assert original.model_dump(mode='json') == before
    candidate = runtime.app_state.config.llm_routing.provider_by_name(registered['provider']['name'])
    assert candidate.auth_mode == 'none' and candidate.extra_body == original.extra_body
    client = factory.build_client_for_provider(candidate, 'synthetic')
    try:
        assert client.prompt('Nonprivate edit-transition control') == 'Synthetic reply'
    finally:
        client.close()
    assert len(native_http[0]) == 1
    assert not {'authorization', 'x-api-key'} & set(native_http[0][0].headers)


def test_paused_catalog_releases_config_lock_and_rejects_late_publication(native_http, connection_home):
    _, runtime = connection_home
    registered = register(task, type='anthropic', base_url='https://example.invalid/native')
    entered, release = Event(), Event()
    def blocked(_request):
        entered.set()
        assert release.wait(3)
    native_http[2]['before_reply'] = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(task.catalog, registered['operation_id'])
        try:
            assert entered.wait(2)
            with store.editing():
                changed = runtime.app_state.config.model_copy(deep=True)
                changed.university_access.login_url = 'https://synthetic.invalid/login'
                store.publish(changed)
        finally:
            release.set()
        with pytest.raises(APIError) as error:
            pending.result(timeout=2)
        assert error.value.error == 'connection_conflict'
    assert store.load(registered['operation_id'])['catalog'] == 'unavailable'


@pytest.mark.parametrize('reason', ['configuration_busy', 'connection_conflict'])
def test_catalog_presend_policy_refusal_is_not_an_invalid_server_response(connection_home, monkeypatch, reason):
    from zotero_summarizer.integrations import llm_models
    registered = register(task, type='anthropic', base_url='https://example.invalid/native')
    def refuse(*args, **kwargs):
        assert callable(kwargs['before_send'])
        raise APIError(reason, 'Publication permission is not current', 409)
    monkeypatch.setattr(llm_models, 'list_anthropic_models', refuse)
    with pytest.raises(APIError) as error:
        task.catalog(registered['operation_id'])
    assert error.value.error == reason
    assert store.load(registered['operation_id'])['catalog'] == 'unavailable'


def test_active_doctor_refuses_credential_write_without_owning_configuration(connection_home, monkeypatch):
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.services.llm import credentials
    settings, runtime = connection_home
    entered, release = Event(), Event()
    writes = []
    def blocked(_settings):
        entered.set()
        assert release.wait(3)
        return doctor._row('environment', 'ready', 'synthetic')
    monkeypatch.setitem(doctor._RUNNERS, 'environment', blocked)
    monkeypatch.setattr(credentials.keyring, 'set_password', lambda *args: writes.append(args))
    with ThreadPoolExecutor(max_workers=2) as pool:
        checking = pool.submit(doctor.run_doctor, settings, check_ids=['environment'])
        try:
            assert entered.wait(2)
            changing = pool.submit(doctor.store_credential_and_invalidate, settings, 'SYNTHETIC_KEY', 'synthetic')
            with pytest.raises(APIError) as error:
                changing.result(timeout=0.5)
            assert error.value.error == 'doctor_running'
            with store.editing():
                changed = runtime.app_state.config.model_copy(deep=True)
                changed.output_language = 'French'
                store.publish(changed)
            assert writes == []
        finally:
            release.set()
        checking.result(timeout=2)


@pytest.mark.parametrize('status', [401, 404])
def test_catalog_exposes_safe_http_observation_without_activation(native_http, connection_home, status):
    registered = register(task, type='anthropic', base_url='https://example.invalid/native')
    native_http[2]['status'] = status
    result = task.catalog(registered['operation_id'])
    assert result['catalog'] == 'unavailable' and result['reason_code'] == 'catalog_http_error'
    assert result['http_status'] == status and f'HTTP {status}' in result['detail']
    assert 'sensitive-provider-body' not in str(result)


@pytest.mark.parametrize('failure,reason', [
    (httpx.ReadTimeout('sensitive timeout'), 'catalog_timeout'),
    (ValueError('sensitive malformed body'), 'catalog_invalid_response'),
])
def test_catalog_timeout_and_malformed_response_remain_distinct(connection_home, monkeypatch, failure, reason):
    from zotero_summarizer.integrations import llm_models
    registered = register(task, base_url='https://example.invalid/native')
    def failed(*args, **kwargs):
        raise llm_models.ModelListError('sensitive wrapper') from failure
    monkeypatch.setattr(llm_models, 'list_openai_models', failed)
    result = task.catalog(registered['operation_id'])
    assert result['reason_code'] == reason and result['catalog'] == 'unavailable'
    assert 'sensitive' not in str(result)


@pytest.mark.parametrize('failure,reason', [('timeout', 'catalog_timeout'), ('malformed', 'catalog_invalid_response')])
def test_actual_native_catalog_failure_is_safe_and_distinct(native_http, connection_home, failure, reason):
    registered = register(task, type='anthropic', base_url='https://example.invalid/native')
    native_http[2].update({'error': httpx.ReadTimeout('sensitive timeout')} if failure == 'timeout' else {'catalog_text': 'sensitive malformed JSON'})
    result = task.catalog(registered['operation_id'])
    assert result['reason_code'] == reason and result['catalog'] == 'unavailable'
    assert len(native_http[0]) == 1 and 'sensitive' not in str(result)


def test_authenticated_empty_registry_bootstrap_can_store_authorization_then_use_and_test(connection_home, monkeypatch):
    from zotero_summarizer.integrations import llm_requests
    from zotero_summarizer.services.llm import connection_credentials, connection_test, credentials
    settings, runtime = connection_home
    settings.config_path.unlink()
    runtime.app_state = None
    memory, requests = {}, []
    monkeypatch.setattr(credentials.keyring, 'set_password', lambda _service, slot, secret: memory.update({slot: secret}))
    monkeypatch.setattr(credentials.keyring, 'get_password', lambda _service, slot: memory.get(slot))
    registered = task.register(RegisterConnection(operation_id=uuid4(), revision=store.revision(), type='anthropic',
        base_url='https://example.invalid/native', auth='new', approved=True))
    assert registered['bootstrap'] and registered['provider'].get('auth_mode', 'legacy') == 'legacy'
    assert not settings.config_path.exists() and memory == {}
    command = ReplaceConnectionCredential(provider=registered['provider']['name'], connection_id=registered['operation_id'], revision=registered['revision'])
    preview = connection_credentials.replace(command)
    stored = connection_credentials.replace(command.model_copy(update={'secret': 'synthetic-new-secret', 'preview': preview['preview']}))
    assert stored['state'] == 'auth_stored' and not settings.config_path.exists()
    receipt = task.readback(registered['operation_id'])
    selection = ConnectionSelection(operation_id=registered['operation_id'], revision=receipt['revision'], model='synthetic')
    impact = task.preview(selection)
    saved = task.apply(selection.model_copy(update={'preview': impact['preview']}))
    def reply(request):
        requests.append(request)
        return httpx.Response(200, json={'id':'msg_synthetic','type':'message','role':'assistant','model':'synthetic',
            'content':[{'type':'text','text':'Synthetic response'}], 'stop_reason':'end_turn',
            'usage':{'input_tokens':1,'output_tokens':1}})
    original = llm_requests.verification_http_client
    monkeypatch.setattr(llm_requests, 'verification_http_client', lambda hook: original(hook, transport=httpx.MockTransport(reply)))
    result = connection_test.test_connection(ModelTestRequest(revision=saved['revision'], model='synthetic'))
    assert result['status'] == 'operational' and len(requests) == 1
    assert requests[0].headers['x-api-key'] == 'synthetic-new-secret'
