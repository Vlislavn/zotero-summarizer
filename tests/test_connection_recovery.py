"""Recovery findings: real native parsing/owned MockTransport, never a live model."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
import tomllib

import httpx
import pytest

from tests.test_connection_native_http import native_http
from tests.test_connection_task import connection_home, register
from tests.test_connection_boundaries import selection
from zotero_summarizer.models.connections import ModelTestRequest
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services import config as config_service
from zotero_summarizer.services._common import write_json_atomic
from zotero_summarizer.services.llm import connections, connection_test, connection_state as store


@pytest.fixture
def saved_native(connection_home):
    registered = register(connections, type='anthropic', base_url='https://example.invalid/exact')
    command = selection(registered, 'synthetic')
    preview = connections.preview(command)
    saved = connections.apply(command.model_copy(update={'preview': preview['preview']}))
    return ModelTestRequest(revision=saved['revision'], model='synthetic')


def test_slow_native_probe_does_not_block_unrelated_config_save(native_http, connection_home, saved_native):
    _, runtime = connection_home
    entered, release = Event(), Event()
    def blocked(_request):
        entered.set()
        assert release.wait(5)
    native_http[2]['before_reply'] = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(connection_test.test_connection, saved_native)
        try:
            assert entered.wait(3)
            draft = runtime.app_state.config.model_copy(deep=True)
            draft.university_access.enabled = True
            # Real full-config publication while the synthetic response is paused.
            saved = asyncio.run(config_service.update_runtime_config(draft, expected_revision=store.revision()))
            assert saved['status'] == 'ok'
        finally:
            release.set()
        result = future.result(timeout=3)
    assert runtime.app_state.config.university_access.enabled is True
    assert result['current'] is False and result['request_completion']['successful'] is True
    assert len(native_http[0]) == 1 and all(client.is_closed for client in native_http[1])


@pytest.mark.parametrize('status', [401, 404, 429, 500])
def test_model_test_discloses_http_status_not_provider_body(native_http, saved_native, status):
    native_http[2]['status'] = status
    result = connection_test.test_connection(saved_native)
    assert result['reason_code'] == 'inference_http_error'
    assert result['http_status'] == status and f'HTTP {status}' in result['detail']
    assert result['recovery'] == 'open_ai_settings' and result['attempted'] is True
    assert result['ownership'] == 'unresolved'  # HTTP error is not a successful native terminal.
    assert 'sensitive-provider-body' not in str(result)
    assert len(native_http[0]) == 1 and all(client.is_closed for client in native_http[1])


@pytest.mark.parametrize('error,reason', [
    (httpx.ReadTimeout('sensitive-timeout'), 'inference_timeout'),
    (httpx.ConnectError('sensitive-connection'), 'inference_transport_error'),
])
def test_model_test_discloses_typed_transport_observation(native_http, saved_native, error, reason):
    native_http[2]['error'] = error
    result = connection_test.test_connection(saved_native)
    assert result['reason_code'] == reason and result['attempted'] is True
    assert result['ownership'] == 'unresolved' and result['request_completion']['hold'] is True
    assert 'sensitive-' not in str(result) and len(native_http[0]) == 1


@pytest.mark.parametrize('updates,reason', [
    ({'text': '   '}, 'inference_empty_response'),
    ({'model': 'different'}, 'request_evidence_invalid'),
    ({'stop_reason': 'max_tokens'}, 'request_output_truncated'),
])
def test_model_test_distinguishes_empty_identity_and_terminal(native_http, saved_native, updates, reason):
    native_http[2].update(updates)
    result = connection_test.test_connection(saved_native)
    assert result['status'] == 'fail' and result['reason_code'] == reason
    assert result['recovery'] == 'open_ai_settings' and result['attempted'] is True
    assert len(native_http[0]) == 1


@pytest.mark.parametrize('provider,reason', [
    (None, 'telegram_selection_missing'),
    ('missing', 'telegram_provider_missing'),
    ('other', 'telegram_endpoint_rejected'),
])
def test_impact_preserves_telegram_rejection_without_blocking_default(connection_home, provider, reason):
    settings, _ = connection_home
    write_json_atomic(settings.data_dir / 'research_feed/profile.json', {
        'schema_version': 1, 'themes': [], 'projects': [],
        'telegram_model': {'provider': provider, 'model': 'telegram'} if provider else None,
    })
    registered = register(connections)
    command = selection(registered)
    preview = connections.preview(command)
    telegram = preview['impact']['telegram']
    assert telegram['admitted'] is False and telegram['reason_code'] == reason
    assert telegram['detail'] and preview['ready_to_apply'] is True
    assert connections.apply(command.model_copy(update={'preview': preview['preview']}))['state'] == 'selection_saved'


def test_sdk_declared_minimums_cover_reused_apis_and_lock_metadata():
    from packaging.requirements import Requirement
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    requirements = {r.name: r for r in map(Requirement, project['project']['dependencies'])}
    lock = tomllib.loads((root / 'uv.lock').read_text())
    owner = next(p for p in lock['package'] if p['name'] == 'zotero-summarizer')
    locked = {p['name']: p['version'] for p in lock['package'] if p['name'] in {'openai', 'anthropic'}}
    for name, minimum, rejected in [('anthropic', '0.105.2', '0.69.0'), ('openai', '2.36.0', '1.104.2')]:
        assert minimum in requirements[name].specifier and rejected not in requirements[name].specifier
        assert locked[name] == minimum
        metadata = next(r for r in owner['metadata']['requires-dist'] if r['name'] == name)
        assert metadata['specifier'] == str(requirements[name].specifier)
        assert {'name': name} in owner['dependencies']


@pytest.mark.parametrize('configured,expected', [(2, 2), (1000, 30)])
def test_probe_transport_budget_uses_configured_input_capped_by_existing_probe_budget(
        native_http, connection_home, saved_native, configured, expected):
    settings, runtime = connection_home
    set_context(AppContext(replace(settings, summary_timeout_seconds=configured), runtime))
    result = connection_test.test_connection(saved_native)
    assert result['status'] == 'operational'
    assert native_http[1][0].timeout.read == expected


def test_changed_revision_refuses_before_credential_lookup(native_http, connection_home, saved_native, monkeypatch):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt
    _, runtime = connection_home
    bind = SetupAttempt.bind_client
    def changed(attempt, *args):
        owner = bind(attempt, *args)
        with store.editing():
            config = runtime.app_state.config.model_copy(deep=True)
            config.output_language = 'French'
            store.publish(config)
        return owner
    monkeypatch.setattr(SetupAttempt, 'bind_client', changed)
    monkeypatch.setattr(factory, 'resolve_api_key', lambda *_: pytest.fail('stale approval cannot look up credentials'))
    result = connection_test.test_connection(saved_native)
    assert result['reason_code'] == 'connection_conflict' and result['attempted'] is False
    assert result['current'] is False and native_http[0] == []


def test_changed_revision_at_native_presend_refuses_without_transport_dispatch(
        native_http, connection_home, saved_native, monkeypatch):
    from zotero_summarizer.integrations import llm_transport
    _, runtime = connection_home
    owned = llm_transport.owned_http_client
    def build(*args, **kwargs):
        client = owned(*args, **kwargs)
        def changed(_request):
            with store.editing():
                config = runtime.app_state.config.model_copy(deep=True)
                config.output_language = 'French'
                store.publish(config)
        client.event_hooks['request'].insert(0, changed)
        return client
    monkeypatch.setattr(llm_transport, 'owned_http_client', build)
    result = connection_test.test_connection(saved_native)
    assert result['reason_code'] == 'connection_conflict' and result['current'] is False
    assert result['attempted'] is False and native_http[0] == []
    assert all(client.is_closed for client in native_http[1])


def test_waiter_timeout_retains_callback_hold_but_not_config_lock(native_http, connection_home, saved_native, monkeypatch):
    from zotero_summarizer.services.llm import operational_check
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt, _WORKER
    _, runtime = connection_home
    entered, release = Event(), Event()
    def blocked(_request):
        entered.set()
        assert release.wait(5)
    native_http[2]['before_reply'] = blocked
    monkeypatch.setattr(operational_check, '_PROBE_TIMEOUT_SECS', 0.1)
    from zotero_summarizer.services.setup import load_attempt
    initial_stamp = iter([0.0])
    def monotonic():
        return next(initial_stamp, operational_check._PROBE_TIMEOUT_SECS if entered.is_set() else 0.0)
    monkeypatch.setattr(load_attempt, 'monotonic', monotonic)
    try:
        result = connection_test.test_connection(saved_native)
        assert entered.is_set() and result['reason_code'] == 'waiter_timeout'
        assert result['ownership'] == 'unresolved' and result['hold'] is True
        other = SetupAttempt().run(lambda: pytest.fail('must not overlap callback'), timeout=0.1)
        assert other['reason_code'] == 'setup_load_busy'
        with store.editing():
            config = runtime.app_state.config.model_copy(deep=True)
            config.output_language = 'French'
            store.publish(config)
    finally:
        release.set()
        assert _WORKER.acquire(timeout=3)
        _WORKER.release()
    assert len(native_http[0]) == 1


@pytest.mark.parametrize('sdk_name', ['openai', 'anthropic'])
def test_safe_http_observation_reuses_real_sdk_status_fields(sdk_name):
    from importlib import import_module
    from zotero_summarizer.services.llm.probe_failure import failure_observation
    from zotero_summarizer.integrations.llm_requests import RequestInventory
    sdk = import_module(sdk_name)
    request = httpx.Request('POST', 'https://example.invalid/native')
    response = httpx.Response(401, request=request, json={'error': 'sensitive-sdk-body'})
    error = sdk.AuthenticationError('sensitive-sdk-message', response=response, body=response.json())
    result = failure_observation(error, RequestInventory('synthetic').summary())
    assert result['http_status'] == 401 and result['reason_code'] == 'inference_http_error'
    assert 'sensitive-' not in str(result)


@pytest.mark.parametrize('kind', ['openai', 'anthropic'])
def test_owned_catalog_reuses_existing_listing_budget(native_http, monkeypatch, kind):
    from zotero_summarizer.integrations import llm_models
    monkeypatch.setattr(llm_models, '_LIST_TIMEOUT_SECS', 2.5)
    if kind == 'openai':
        assert llm_models.list_openai_models('https://example.invalid/exact', None, owned=True) == ['served']
    else:
        assert llm_models.list_anthropic_models(None, owned=True) == ['served']
    assert native_http[1][0].timeout.read == 2.5
