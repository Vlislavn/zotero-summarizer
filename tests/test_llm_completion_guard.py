"""The installed LangChain callback path must propagate truncation, not swallow it."""

import httpx
import pytest
from langchain_openai import ChatOpenAI

from zotero_summarizer.integrations.llm_callbacks import CompletionGuard, LLMOutputTruncated, observe_completions


@pytest.mark.parametrize("content", [None, '{"ok":'])
def test_completion_guard_rejects_truncation_but_accepts_complete_response(content):
    reason = "length"

    def respond(request):
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "finish_reason": reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 16, "total_tokens": 26},
        })

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        llm = ChatOpenAI(model="test", api_key="test", http_client=client,
                         callbacks=[CompletionGuard()], max_retries=0)
        with pytest.raises(LLMOutputTruncated, match="output token limit"):
            llm.invoke("Return JSON")
        reason, content = "stop", '{"ok":true}'
        assert llm.invoke("Return JSON").content == content


@pytest.mark.parametrize('response_format', [{'type': 'json_object'}, {'type': 'json_schema', 'json_schema': {
    'name': 'Reply', 'strict': True, 'schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                                           'required': ['ok'], 'additionalProperties': False}}}])
@pytest.mark.parametrize('observed_model', ['test', 'foreign'])
def test_sdk_structured_length_preserves_actual_terminal_and_usage_without_success(response_format, observed_model):
    from zotero_summarizer.integrations.llm_requests import RequestInventory, owned_request, verification_http_client
    inventory = RequestInventory('sdk-structured')
    guard = CompletionGuard(expected_model='test', request_owner=lambda: inventory.expect(
        'configured', 'test', 'https://example.invalid/v1/chat/completions', local=False))
    observations = []
    def reply(_request):
        return httpx.Response(200, json={'id': 'native-result', 'object': 'chat.completion', 'created': 0, 'model': observed_model,
            'choices': [{'index': 0, 'finish_reason': 'length', 'message': {'role': 'assistant', 'content': '{'}}],
            'usage': {'prompt_tokens': 21, 'completion_tokens': 1, 'total_tokens': 22}})
    with verification_http_client(guard.transport_started, transport=httpx.MockTransport(reply)) as http:
        llm = ChatOpenAI(model='test', api_key='test', base_url='https://example.invalid/v1',
                         http_client=http, callbacks=[guard], max_retries=0)
        rejection = LLMOutputTruncated if observed_model == 'test' else ValueError
        with observe_completions(observations.append), pytest.raises(rejection), owned_request(guard):
            llm.invoke('Public structured negative control', response_format=response_format)
    assert guard.completed is False
    if observed_model != 'test':
        assert not observations and inventory.summary()['hold'] and inventory.summary()['invalid_events']
        assert not inventory.receipts()[0]['terminal']
        return
    assert len(observations) == 1
    assert observations[0].llm_output['token_usage']['completion_tokens'] == 1
    assert observations[0].llm_output['native_completion_source'] == 'sdk_length_error'
    assert inventory.summary()['coherent'] and not inventory.summary()['hold']
    assert not inventory.summary()['successful']
    assert inventory.receipts()[0]['reason_code'] == 'request_output_truncated'


def test_sdk_timeout_without_native_completion_keeps_unknown_hold():
    from openai import APITimeoutError
    from zotero_summarizer.integrations.llm_requests import RequestInventory, owned_request, verification_http_client
    inventory = RequestInventory('sdk-timeout')
    guard = CompletionGuard(expected_model='test', request_owner=lambda: inventory.expect(
        'configured', 'test', 'https://example.invalid/v1/chat/completions', local=False))
    observations = []
    def reply(request):
        raise httpx.ReadTimeout('Synthetic timeout', request=request)
    with verification_http_client(guard.transport_started, transport=httpx.MockTransport(reply)) as http:
        llm = ChatOpenAI(model='test', api_key='test', base_url='https://example.invalid/v1',
                         http_client=http, callbacks=[guard], max_retries=0)
        with observe_completions(observations.append), pytest.raises(APITimeoutError), owned_request(guard):
            llm.invoke('Public timeout control', response_format={'type': 'json_object'})
    assert not observations and not guard.completed
    assert inventory.summary()['hold'] and not inventory.receipts()[0]['terminal']


def test_owned_truncation_retains_native_usage_before_refusal():
    observations = []
    guard = CompletionGuard(expected_model="test")

    def respond(_request):
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "finish_reason": "length",
                         "message": {"role": "assistant", "content": '{"incomplete":'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 16384, "total_tokens": 16484},
        })

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        llm = ChatOpenAI(model="test", api_key="test", http_client=client,
                         callbacks=[guard], max_retries=0)
        with observe_completions(observations.append), pytest.raises(LLMOutputTruncated):
            llm.invoke("Return a complete structured review")
    assert len(observations) == 1
    assert observations[0].llm_output["token_usage"]["completion_tokens"] == 16384
    assert guard.completed is False
