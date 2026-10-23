"""Synthetic protocol/HTTP fixtures; no live capability or semantic grading."""

import ast
import base64
import hashlib
import json
import sys
import types
from pathlib import Path

import httpx
import pytest
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel

from tools.method_validation.client import ClientError, ClientOptions, MethodValidationClient

SOURCE = Path('/Users/vladnikulin/.pi/agent/skills/biomni-codex-subscription/scripts/pi_codex_transport.py')
SOURCE_SHA = 'ac33d26ff917d90dcf629e461342f5c9828322cae8b846da2e49320a00f286ed'
SITE = Path('/Users/vladnikulin/code/personal/zotero-summarizer/.venv/lib/python3.13/site-packages')
BASE_SHA = 'a0d3cf6389798b7cd10d9aae010b13e14e06b3cccbb261d5bcd78b5e53f2659c'
HELPERS_SHA = 'db6f151f4170d612e75d52399f856d20bd4979ecfc99e708871cb3ac9eb895b4'


class Output(BaseModel):
    text: str


def options(**overrides):
    return ClientOptions(**{
        'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh', 'session_id': 'synthetic-session',
        'max_logical_calls': 3, 'source_path': SOURCE, 'source_sha256': SOURCE_SHA, **overrides,
    })


@pytest.fixture
def real_formatting_fixture(monkeypatch):
    # Execute only the exact pure method/helper bodies: not a real onprem import proof.
    base = SITE / 'onprem/llm/base.py'
    helpers = SITE / 'onprem/llm/helpers.py'
    assert hashlib.sha256(base.read_bytes()).hexdigest() == BASE_SHA
    assert hashlib.sha256(helpers.read_bytes()).hexdigest() == HELPERS_SHA
    llm = next(node for node in ast.parse(base.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == 'LLM')
    method = next(node for node in llm.body if isinstance(node, ast.FunctionDef)
                  and node.name == '_format_pydantic_prompt')
    namespace = {'PydanticOutputParser': PydanticOutputParser, 'PromptTemplate': PromptTemplate}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(base), 'exec'), namespace)
    functions = [node for node in ast.parse(helpers.read_text()).body
                 if isinstance(node, ast.FunctionDef)
                 and node.name in {'extract_json', '_marshal_llm_to_json', '_complete_json'}]
    helper_module = types.ModuleType('onprem.llm.helpers')
    helper_module.json = json
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(helpers), 'exec'), helper_module.__dict__)
    base_module = types.ModuleType('onprem.llm.base')
    base_module.LLM = type('FormattingOnlyLLM', (), {'_format_pydantic_prompt': namespace['_format_pydantic_prompt']})
    monkeypatch.setitem(sys.modules, 'onprem.llm.base', base_module)
    monkeypatch.setitem(sys.modules, 'onprem.llm.helpers', helper_module)
    return namespace['_format_pydantic_prompt']


def sse(text, model='gpt-6.1-sol', usage='default', status='completed'):
    usage = usage if usage != 'default' else {
        'input_tokens': 21, 'output_tokens': 34, 'total_tokens': 55,
        'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 19},
    }
    response = {'id': 'resp_synthetic', 'object': 'response', 'created_at': 0,
                'status': status, 'model': model, 'output': [], 'usage': usage}
    events = [
        {'type': 'response.created', 'response': {**response, 'status': 'in_progress'}},
        {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0,
         'item_id': 'msg_synthetic', 'delta': text},
        {'type': 'response.completed', 'response': response},
    ]
    return ''.join(f'data: {json.dumps(event)}\n\n' for event in events).encode()


@pytest.fixture
def transport(monkeypatch):
    from langchain_openai import ChatOpenAI  # import before intercepting constructors

    assert ChatOpenAI is not None
    requests, responses, credentials, clients = [], [], [], []
    payload = base64.urlsafe_b64encode(json.dumps({
        'https://api.openai.com/auth': {'chatgpt_account_id': 'SYNTHETIC-NOT-AN-ACCOUNT'},
    }).encode()).decode().rstrip('=')
    fake_token = f'SYNTHETIC.{payload}.NOT-A-REAL-SIGNATURE'

    def resolve(_provider):
        credentials.append('synthetic-resolution')
        return fake_token

    def handler(request):
        requests.append(json.loads(request.content))
        response = responses.pop(0) if responses else sse('complete')
        if isinstance(response, Exception):
            raise response
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, content=response, headers={'content-type': 'text/event-stream'})

    original_init = httpx.Client.__init__

    def client_init(client, *args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        original_init(client, *args, **kwargs)
        clients.append(client)

    monkeypatch.setattr(httpx.Client, '__init__', client_init)
    monkeypatch.setattr(httpx.AsyncClient, '__init__', lambda *a, **k: pytest.fail('async client created'))
    client = MethodValidationClient(options())
    monkeypatch.setattr(client._source.PiCodexTokenProvider, 'resolve', resolve)
    return client, requests, responses, credentials, clients, fake_token


def test_neutral_text_stock_transform_and_safe_actual_request_receipt(transport, record_property):
    client, requests, responses, credentials, clients, fake_token = transport
    events = []
    client.observer = events.append
    text = json.dumps({'text': 'literal </execute> and </solution> remain complete', 'after': 'tail'})
    assert client._source.truncate_biomni_xml_turn(text) != text
    responses.append(sse(text))
    assert credentials == clients == []

    with client:
        assert client.prompt('  PUBLIC prompt\n') == text

    assert clients[0].is_closed
    assert requests[0]['input'][0]['content'] == '  PUBLIC prompt\n'
    assert requests[0]['reasoning']['effort'] == 'xhigh'
    receipt = events[-1]
    assert receipt['status'] == 'success'
    assert receipt['original_prompt'] == receipt['request_prompt'] == '  PUBLIC prompt\n'
    assert receipt['returned_content'] == text
    assert receipt['observed_model'] == 'gpt-6.1-sol'
    assert receipt['requests'][0] == {
        'scope': 'httpx_request_hook_pre_dispatch', 'model': 'gpt-6.1-sol', 'effort': 'xhigh',
        'session_id': 'synthetic-session', 'store': False, 'stream': True,
    }
    assert receipt['usage'] == {'input_tokens': 21, 'output_tokens': 34, 'total_tokens': 55,
                               'cache_read_tokens': 0, 'reasoning_tokens': 19,
                               'cache_creation_input_tokens': None}
    assert receipt['attempts'] == [{'attempt': 1, 'status': 'response_received', 'http_request_count': 1}]
    assert fake_token not in json.dumps(events)
    assert 'SYNTHETIC-NOT-AN-ACCOUNT' not in json.dumps(events)
    assert 'authorization' not in json.dumps(events).lower()
    record_property('synthetic_http_request', json.dumps(requests[0], sort_keys=True))
    record_property('safe_terminal_receipt', json.dumps(receipt, sort_keys=True))
    record_property('stock_truncated_output', client._source.truncate_biomni_xml_turn(text))


@pytest.mark.parametrize('url', [
    'http://chatgpt.com/backend-api/codex/responses', 'https://example.test/backend-api/codex/responses',
    'https://chatgpt.com:444/backend-api/codex/responses', 'https://chatgpt.com/backend-api/codex/other',
    'https://chatgpt.com/backend-api/codex/responses?query=yes',
    'https://user:password@chatgpt.com/backend-api/codex/responses',
])
def test_forbidden_destination_before_credentials(transport, url):
    client, requests, _, credentials, _, _ = transport
    auth = client._auth()
    with pytest.raises(ClientError, match='destination'):
        next(auth.sync_auth_flow(httpx.Request('POST', url)))
    assert credentials == requests == []


@pytest.mark.parametrize('kwargs', [
    {'response_format': {'type': 'json_schema'}}, {'max_tokens': 1}, {'max_output_tokens': None},
    {'stop': ['END']}, {'temperature': 0}, {'attempt_fix': True}, {'fix_llm': object()},
    {'model': 'other'}, {'extra_body': {'model': 'other'}},
])
def test_unsupported_semantics_before_network(transport, kwargs):
    client, requests, _, credentials, clients, _ = transport
    with pytest.raises(ValueError):
        client.prompt('public', **kwargs)
    assert requests == credentials == clients == []
    assert client.logical_calls == 0


@pytest.mark.parametrize('change', [
    {'reasoning_effort': 'high'}, {'max_logical_calls': 0}, {'max_logical_calls': True},
    {'max_logical_calls': 10001}, {'operation_timeout_seconds': 0}, {'operation_timeout_seconds': 601},
    {'operation_timeout_seconds': True}, {'model': ''}, {'session_id': ''}, {'source_sha256': 'short'},
])
def test_config_bounds(change):
    with pytest.raises(ValueError):
        options(**change)


def test_env_timeout_and_source_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv('ZS_METHOD_OPERATION_TIMEOUT_SECONDS', '42')
    assert options().operation_timeout_seconds == 42
    monkeypatch.setenv('ZS_METHOD_OPERATION_TIMEOUT_SECONDS', 'invalid')
    with pytest.raises(ValueError):
        options()
    monkeypatch.delenv('ZS_METHOD_OPERATION_TIMEOUT_SECONDS')
    source = tmp_path / 'transport.py'
    source.write_text('raise AssertionError("must not execute")')
    with pytest.raises(ClientError, match='source'):
        MethodValidationClient(options(source_path=source))


def test_loaded_source_drift_before_dispatch(tmp_path):
    source = tmp_path / 'bound_transport.py'
    source.write_bytes(SOURCE.read_bytes())
    client = MethodValidationClient(options(source_path=source))
    source.write_bytes(source.read_bytes() + b'\n# synthetic drift\n')
    with pytest.raises(ClientError, match='source'):
        client.prompt('public')
    assert client._http is None
    assert client.logical_calls == 0


@pytest.mark.parametrize('stop', [None, []])
def test_empty_stop_budget_and_stable_session(transport, stop):
    client, requests, _, credentials, _, _ = transport
    for _ in range(3):
        assert client.prompt('public', stop=stop) == 'complete'
    with pytest.raises(ClientError, match='budget'):
        client.prompt('no fourth request')
    assert len(credentials) == len(requests) == client.logical_calls == 3
    assert {request['prompt_cache_key'] for request in requests} == {'synthetic-session'}
    client.close()
    with pytest.raises(ClientError, match='closed'):
        client.prompt('public')


@pytest.mark.parametrize('text,expected', [
    ('{"text":"literal </execute> then </solution> and tail"}', Output(text='literal </execute> then </solution> and tail')),
    ('Narrative ```json\n{"text":"value"}\n```', Output(text='value')),
    ('not parseable JSON', 'not parseable JSON'),
    ('{"text": {"wrong": "type"}}', '{"text": {"wrong": "type"}}'),
])
def test_real_formatter_parity_and_raw_parse_failure(transport, real_formatting_fixture, text, expected, record_property):
    client, requests, responses, _, _, _ = transport
    events = []
    client.observer = events.append
    responses.append(sse(text))
    prompt = '  untouched PUBLIC query\n'
    actual_prompt, _ = real_formatting_fixture(None, prompt, Output)

    with client:
        assert client.pydantic_prompt(prompt, Output) == expected

    assert len(requests) == client.logical_calls == 1
    assert requests[0]['input'][0]['content'].encode() == actual_prompt.encode()
    assert events[-1]['original_prompt'] == prompt
    assert events[-1]['request_prompt'] == actual_prompt
    assert events[-1]['returned_content'] == text
    assert events[-1]['status'] == ('parse_failure' if isinstance(expected, str) else 'success')
    record_property('fixture_only_safe_receipt', json.dumps(events[-1], sort_keys=True))


def test_model_mismatch_saves_receipt_before_raise(transport):
    client, requests, responses, _, _, _ = transport
    events = []
    client.observer = events.append
    responses.append(sse('complete', model='other-model'))
    with client, pytest.raises(ClientError, match='model'):
        client.prompt('public')
    assert len(requests) == 1
    assert events[-1]['status'] == 'model_mismatch'
    assert events[-1]['returned_content'] == 'complete'
    assert events[-1]['usage']['total_tokens'] == 55


@pytest.mark.parametrize('failure', [httpx.ReadError('secret must not be emitted'),
                                      httpx.ReadTimeout('secret must not be emitted')])
def test_no_retry_and_safe_terminal_failure(transport, failure):
    client, requests, responses, _, _, _ = transport
    events = []
    client.observer = events.append
    responses.append(failure)
    with client, pytest.raises(ClientError):
        client.prompt('public')
    assert len(requests) == client.logical_calls == 1
    assert events[-1]['status'] == 'transport_failure'
    assert events[-1]['attempts'] == [{'attempt': 1, 'status': 'terminal_failure', 'http_request_count': 1}]
    assert all(value is None for value in events[-1]['usage'].values())
    assert 'secret must not be emitted' not in json.dumps(events)


def test_missing_usage_is_unavailable_not_zero(transport):
    client, _, responses, _, _, _ = transport
    events = []
    client.observer = events.append
    responses.append(sse('complete', usage=None))
    with client:
        assert client.prompt('public') == 'complete'
    assert all(value is None for value in events[-1]['usage'].values())


def test_disabled_async_and_close_on_body_exception(transport):
    import asyncio

    client, _, _, _, clients, _ = transport
    with pytest.raises(ValueError, match='caller'):
        with client:
            client.prompt('public')
            with pytest.raises(ClientError, match='synchronous'):
                asyncio.run(client._model.ainvoke('public'))
            raise ValueError('caller')
    assert clients[0].is_closed
    client.close()


@pytest.mark.parametrize('status', [401, 429, 500, 307])
def test_http_failures_never_retry_or_follow_redirect(transport, status):
    client, requests, responses, credentials, _, _ = transport
    events = []
    client.observer = events.append
    responses.append(httpx.Response(status, json={'error': {'message': 'opaque provider secret'}},
                                    headers={'location': 'https://example.test/elsewhere'}))
    with client, pytest.raises(ClientError):
        client.prompt('public')
    assert len(requests) == len(credentials) == 1
    assert events[-1]['status'] == 'transport_failure'
    assert 'opaque provider secret' not in json.dumps(events)


def test_observer_receipts_are_detached_and_preserve_contiguous_calls(transport):
    client, requests, _, _, _, _ = transport
    events = []

    def capture(receipt):
        events.append(receipt)
        if receipt['event'] == 'call_started':
            receipt['requests'].append({'observer_owned': True})

    client.observer = capture
    with client:
        client.prompt('public one')
        client.prompt('public two')
    terminals = [event for event in events if event['event'] == 'call_terminal']
    assert [event['logical_call'] for event in terminals] == [1, 2]
    assert [event['attempts'][0]['attempt'] for event in terminals] == [1, 1]
    assert all(len(event['requests']) == 1 and 'observer_owned' not in event['requests'][0]
               for event in terminals)
    assert len(requests) == 2


def test_length_failure_preserves_receipt_and_existing_typed_boundary(transport, monkeypatch):
    from langchain_core.messages import AIMessage
    from zotero_summarizer.integrations.llm_callbacks import LLMOutputTruncated

    client, _, _, _, _, _ = transport
    events = []
    client.observer = events.append
    model = types.SimpleNamespace(invoke=lambda prompt, **kwargs: AIMessage(content='partial', response_metadata={
        'model_name': 'gpt-6.1-sol', 'finish_reason': 'length'}))
    monkeypatch.setattr(client, '_build_model', lambda: model)
    with client, pytest.raises(LLMOutputTruncated):
        client.prompt('public')
    assert events[-1]['returned_content'] == 'partial'
    assert events[-1]['status'] == 'incomplete_response'


@pytest.mark.parametrize('usage', [
    {'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 9},
    {'input_tokens': True, 'output_tokens': 3, 'total_tokens': 4},
    {'input_tokens': -1, 'output_tokens': 3, 'total_tokens': 2},
])
def test_invalid_provider_usage_receipt_before_rejection(transport, monkeypatch, usage):
    client, _, _, _, _, _ = transport
    events = []
    client.observer = events.append
    message = types.SimpleNamespace(content='complete', response_metadata={'model_name': 'gpt-6.1-sol'},
                                    usage_metadata=usage)
    monkeypatch.setattr(client, '_build_model', lambda: types.SimpleNamespace(invoke=lambda prompt, **kwargs: message))
    with client, pytest.raises(ClientError, match='invalid_usage'):
        client.prompt('public')
    assert events[-1]['usage_status'] == 'invalid'
    assert events[-1]['returned_content'] == 'complete'


@pytest.mark.parametrize('fails', [False, True])
def test_scoped_tracing_suppression_restores_inherited_context(transport, monkeypatch, fails):
    from contextvars import copy_context
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.callbacks import manager
    from langsmith.run_helpers import get_tracing_context, tracing_context

    client, requests, responses, _, _, _ = transport
    attempts, contexts, events = [], [], []

    class ForbiddenRemoteTracer(BaseCallbackHandler):
        raise_error = True

        def on_chat_model_start(self, *args, **kwargs):
            attempts.append('external tracing')
            raise AssertionError('external tracing forbidden')

    tracer = ForbiddenRemoteTracer()
    token = manager.tracing_v2_callback_var.set(tracer)
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'true')
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    original_hook = client._observe_request

    def capture_context(request):
        assert manager.tracing_v2_callback_var.get() is None
        assert other_context.run(manager.tracing_v2_callback_var.get) is tracer
        contexts.append(get_tracing_context())
        original_hook(request)

    monkeypatch.setattr(client, '_observe_request', capture_context)
    client.observer = events.append
    if fails:
        responses.append(httpx.ReadError('synthetic failure'))
    try:
        with tracing_context(enabled=True, client=object()):
            before = get_tracing_context()
            other_context = copy_context()
            with client:
                if fails:
                    with pytest.raises(ClientError):
                        client.prompt('public')
                else:
                    assert client.prompt('public') == 'complete'
            assert get_tracing_context() == before
            assert manager.tracing_v2_callback_var.get() is tracer
    finally:
        manager.tracing_v2_callback_var.reset(token)
    assert attempts == []
    assert len(requests) == 1
    assert contexts[0]['enabled'] is False and contexts[0]['parent'] is None
    assert contexts[0]['client'] is None
    assert events[-1]['event'] == 'call_terminal'


def test_global_cache_cannot_falsely_count_inference(transport, monkeypatch):
    from langchain_core.globals import get_llm_cache, set_llm_cache
    from langchain_core.caches import BaseCache

    client, requests, _, _, _, _ = transport
    lookups = []

    class ForbiddenCache(BaseCache):
        def lookup(self, *args):
            lookups.append('cached-return')
            raise AssertionError('global cache must not supply a fake inference')

        def update(self, *args):
            raise AssertionError('global cache mutation forbidden')

        def clear(self, **kwargs):
            raise AssertionError('global cache mutation forbidden')

    previous = get_llm_cache()
    set_llm_cache(ForbiddenCache())
    try:
        with client:
            assert client.prompt('public') == 'complete'
            assert client.prompt('public') == 'complete'
    finally:
        set_llm_cache(previous)
    assert lookups == []
    assert len(requests) == 2


@pytest.mark.parametrize('failure', ['parse_failure', 'model_mismatch'])
def test_tracing_restored_after_parser_or_model_failure(transport, real_formatting_fixture, failure):
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import tracing_v2_callback_var
    from langsmith.run_helpers import get_tracing_context, tracing_context

    client, requests, responses, _, _, _ = transport
    events = []
    client.observer = events.append
    responses.append(sse('not parseable JSON', model='other' if failure == 'model_mismatch' else 'gpt-6.1-sol'))
    class ForbiddenFormatterTracer(BaseCallbackHandler):
        raise_error = True

        def on_chain_start(self, *args, **kwargs):
            pytest.fail('formatter prompt escaped to inherited tracer')

    sentinel = ForbiddenFormatterTracer()
    token = tracing_v2_callback_var.set(sentinel)
    try:
        with tracing_context(enabled=True):
            before = get_tracing_context()
            with client:
                if failure == 'model_mismatch':
                    with pytest.raises(ClientError, match='model'):
                        client.pydantic_prompt('public', Output)
                else:
                    assert client.pydantic_prompt('public', Output) == 'not parseable JSON'
            assert tracing_v2_callback_var.get() is sentinel
            assert get_tracing_context() == before
    finally:
        tracing_v2_callback_var.reset(token)
    assert len(requests) == 1
    assert events[-1]['status'] == failure
