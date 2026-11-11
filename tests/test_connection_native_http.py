"""No-auth native HTTP must never construct SDK auth/profile machinery."""
import json

import httpx
import pytest
from pydantic import BaseModel

from zotero_summarizer.integrations import llm_models, llm_transport
from zotero_summarizer.integrations.llm_anthropic import AnthropicLLMClient
from tests.test_connection_task import connection_home, register


class NativeReply(BaseModel):
    accepted: bool


@pytest.fixture
def native_http(tmp_path, monkeypatch):
    import anthropic
    from anthropic.lib.credentials import _constants
    monkeypatch.setenv('HOME', str(tmp_path))
    def forbidden(*a, **kw):
        pytest.fail('No-auth must not enter native SDK constructor/ambient pointer discovery')
    monkeypatch.setattr(anthropic, 'Anthropic', forbidden)
    monkeypatch.setattr(_constants, '_read_active_config_pointer', forbidden)
    requests, transports = [], []
    result = {'status': 200, 'stop_reason': 'end_turn', 'model': 'synthetic'}
    def reply(request):
        requests.append(request)
        if result.get('before_reply'):
            result['before_reply'](request)
        if result.get('error'):
            raise result['error']
        if result['status'] != 200:
            return httpx.Response(result['status'], text='sensitive-provider-body',
                                  headers={'location': 'https://unapproved.invalid/'})
        if request.method == 'GET':
            if 'catalog_text' in result:
                return httpx.Response(200, text=result['catalog_text'])
            return httpx.Response(200, json={'data': [{'id': 'served'}], 'has_more': False})
        body = json.loads(request.content)
        text = result.get('text', '{"accepted":true}' if 'output_config' in body else 'Synthetic reply')
        return httpx.Response(200, json={'id': 'msg_owned', 'type': 'message', 'role': 'assistant',
            'model': result['model'], 'content': [{'type': 'text', 'text': text}],
            'stop_reason': result['stop_reason'], 'usage': {'input_tokens': 2, 'output_tokens': 2}})
    owned = llm_transport.owned_http_client
    def build(*args, **kwargs):
        client = owned(*args, **kwargs, transport=httpx.MockTransport(reply))
        transports.append(client)
        return client
    monkeypatch.setattr(llm_transport, 'owned_http_client', build)
    return requests, transports, result


def test_separate_saved_native_model_probe_reports_owned_response_without_reroute(native_http, connection_home):
    from zotero_summarizer.models.connections import ConnectionSelection, ModelTestRequest
    from zotero_summarizer.services.llm import connections, connection_test
    registered = register(connections, type='anthropic', base_url='https://example.invalid/exact')
    command = ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'], model='synthetic',
                                  corrections={'feed': 'previous', 'backlog': 'previous'})
    preview = connections.preview(command)
    saved = connections.apply(command.model_copy(update={'preview': preview['preview']}))
    result = connection_test.test_connection(ModelTestRequest(revision=saved['revision'], model='synthetic'))
    assert result['status'] == 'operational' and result['response'] == 'Synthetic reply'
    assert result['request_completion']['successful'] is True and result['current'] is True
    requests, transports, _ = native_http
    assert len(requests) == 1 and requests[0].url.path == '/exact/v1/messages'
    assert all(client.is_closed for client in transports)


def test_named_task_probe_reuses_native_owner_once_without_testing_default(native_http, connection_home):
    from zotero_summarizer.models.connections import ConnectionSelection, TaskModelsTestRequest
    from zotero_summarizer.services.llm import connections, connection_test
    _, runtime = connection_home
    original_default = runtime.app_state.config.llm_routing.default.model_copy()
    registered = register(connections, type='anthropic', base_url='https://example.invalid/exact')
    choice = ConnectionSelection(operation_id=registered['operation_id'], revision=registered['revision'],
                                 model='synthetic', targets=['feed', 'deep_review'])
    preview = connections.preview(choice)
    saved = connections.apply(choice.model_copy(update={'preview': preview['preview']}))
    result = connection_test.test_tasks(TaskModelsTestRequest(revision=saved['revision'], stages=['feed', 'deep_review']))
    assert result['status'] == 'ok' and result['current'] is True
    assert result['request_completion']['successful'] is True
    assert [row['stage'] for row in result['stages']] == ['feed', 'deep_review']
    assert all(row['status'] == 'operational' and row['request_completion']['successful'] for row in result['stages'])
    assert runtime.app_state.config.llm_routing.default == original_default
    requests, transports, _ = native_http
    assert len(requests) == 1 and requests[0].url.path == '/exact/v1/messages'
    assert all(client.is_closed for client in transports)


def test_native_no_auth_raw_structured_and_catalog_without_sdk_or_home_read(native_http):
    requests, transports, _ = native_http
    client = AnthropicLLMClient(model='synthetic', api_key=None, base_url='https://example.invalid/exact', no_auth=True, timeout=2)
    try:
        assert client.prompt('Nonprivate raw') == 'Synthetic reply'
        assert client.pydantic_prompt('Nonprivate structured', NativeReply).accepted is True
    finally:
        client.close()
    assert llm_models.list_anthropic_models(None, None, owned=True) == ['served']
    assert [r.url.path for r in requests] == ['/exact/v1/messages', '/exact/v1/messages', '/v1/models']
    payload = json.loads(requests[1].content)
    from anthropic.lib._parse._transform import transform_schema
    assert payload['output_config'] == {'format': {'type': 'json_schema', 'schema': transform_schema(NativeReply.model_json_schema())}}
    assert 'temperature' not in payload
    assert all(r.headers['anthropic-version'] == '2023-06-01' for r in requests)
    assert all(not {'authorization', 'x-api-key'} & set(r.headers) for r in requests)
    assert all(client.is_closed for client in transports)


@pytest.mark.parametrize('stop', ['refusal', 'max_tokens', None])
def test_native_no_auth_never_accepts_missing_or_unsuccessful_terminal(native_http, stop):
    requests, transports, result = native_http
    result['stop_reason'] = stop
    client = AnthropicLLMClient(model='synthetic', api_key=None, no_auth=True, timeout=2)
    try:
        with pytest.raises(ValueError):
            client.prompt('Nonprivate refused control')
    finally:
        client.close()
    assert len(requests) == 1 and transports[0].is_closed


def test_native_no_auth_verification_retains_request_and_terminal_inventory(native_http):
    requests, transports, _ = native_http
    from zotero_summarizer.integrations.llm_requests import RequestInventory
    inventory = RequestInventory('synthetic-owner')
    owner = lambda: inventory.expect('synthetic', 'synthetic', 'https://api.anthropic.com/v1/messages', local=False)
    client = AnthropicLLMClient(model='synthetic', api_key=None, no_auth=True, verification=True, request_owner=owner, timeout=2)
    try:
        assert client.prompt('Nonprivate inventory control') == 'Synthetic reply'
    finally:
        client.close()
    assert len(requests) == 1 and all(client.is_closed for client in transports)
    assert inventory.summary()['successful'] is True
    assert inventory.receipts()[0]['started'] and inventory.receipts()[0]['terminal']


@pytest.mark.parametrize('status', [302, 401, 500])
def test_native_no_auth_catalog_refuses_redirect_or_http_failure_once(native_http, status):
    requests, transports, result = native_http
    result['status'] = status
    with pytest.raises(llm_models.ModelListError):
        llm_models.list_anthropic_models(None, owned=True)
    assert len(requests) == 1 and all(client.is_closed for client in transports)
