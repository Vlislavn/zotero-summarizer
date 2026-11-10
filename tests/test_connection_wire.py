"""Installed OpenAI SDK/native HTTP serialization; owned mock HTTP only."""
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.llm import factory
from zotero_summarizer.integrations import llm_transport, llm_models


@pytest.fixture
def wire(monkeypatch):
    for name in ('OPENAI_API_KEY', 'OPENAI_ADMIN_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
        monkeypatch.setenv(name, 'ambient-secret')
    for name in ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'OPENAI_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://unapproved.invalid/path')
    for name in ('OPENAI_CUSTOM_HEADERS', 'ANTHROPIC_CUSTOM_HEADERS'):
        monkeypatch.setenv(name, 'Authorization: ambient-bearer\nX-Api-Key: ambient-key\nX-Private: ambient-private\nX-Stainless-Secret: ambient-stainless\nX-Stainless-Raw-Response: ambient-raw\nUser-Agent: ambient-agent\nanthropic-beta: ambient-beta\nanthropic-version: ambient-version')
    captured = []
    def reply(request):
        captured.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'synthetic', 'type': 'model', 'display_name': 'Synthetic', 'created_at': '2026-01-01T00:00:00Z'}], 'has_more': False})
        if '/messages' in request.url.path:
            return httpx.Response(200, json={'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant', 'model': 'synthetic',
                'content': [{'type': 'text', 'text': 'Synthetic reply'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 2, 'output_tokens': 2}})
        content = '{"accepted":true}' if 'response_format' in json.loads(request.content) else 'Synthetic reply'
        return httpx.Response(200, json={'id': 'chatcmpl_synthetic', 'object': 'chat.completion', 'created': 0, 'model': 'synthetic',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 2, 'total_tokens': 4}})
    original = llm_transport.owned_http_client
    monkeypatch.setattr(llm_transport, 'owned_http_client', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(factory, 'get_api_key', lambda *a: pytest.fail('no auth cannot resolve secrets'))
    monkeypatch.setattr(factory, 'settings', lambda: SimpleNamespace(summary_timeout_seconds=2))
    return captured


@pytest.mark.parametrize('protocol,base,path', [
    ('openai', 'https://example.invalid/exact/proxy', '/exact/proxy/chat/completions'),
    ('anthropic', 'https://example.invalid/exact/proxy', '/exact/proxy/v1/messages'),
    ('anthropic', None, '/v1/messages'),
])
def test_no_auth_serialization_does_not_inherit_headers_or_destinations(wire, monkeypatch, protocol, base, path):
    if protocol == 'openai':
        for name in list(sys.modules):
            if name == 'onprem' or name.startswith('onprem.'):
                monkeypatch.delitem(sys.modules, name)
        from onprem.llm import LLM
        from zotero_summarizer.services import _adapters
        monkeypatch.setattr(_adapters, '_load_onprem', lambda: (LLM, None))
    provider = ProviderConfig(name='none', type=protocol, base_url=base, api_key_env='NEVER_READ', auth_mode='none')
    client = factory.build_client_for_provider(provider, 'synthetic')
    try:
        assert client.prompt('Nonprivate synthetic request') == 'Synthetic reply'
    finally:
        client.close()
    assert len(wire) == 1
    assert wire[0].url.path == path
    assert wire[0].url.host == ('api.anthropic.com' if base is None else 'example.invalid')
    assert not {'authorization', 'x-api-key', 'x-private'} & set(wire[0].headers)
    assert not any('ambient' in value for value in wire[0].headers.values())
    assert json.loads(wire[0].content)['model'] == 'synthetic'
    if protocol == 'openai':
        assert wire[0].headers['x-stainless-raw-response'] == 'true'


@pytest.mark.parametrize('protocol,base,path', [
    ('openai', 'https://example.invalid/proxy/root', '/proxy/root/models'),
    ('anthropic', None, '/v1/models'),
])
def test_owned_metadata_has_no_ambient_auth(wire, protocol, base, path):
    ids = llm_models.list_openai_models(base, None, owned=True) if protocol == 'openai' else llm_models.list_anthropic_models(None, base, owned=True)
    assert ids == ['synthetic']
    assert len(wire) == 1 and wire[0].url.path == path
    assert not any('ambient' in value for value in wire[0].headers.values())
    assert not {'authorization', 'x-api-key', 'x-private'} & set(wire[0].headers)


def test_owned_no_auth_openai_keeps_the_sdk_root_for_structured_output(wire, monkeypatch):
    from pydantic import BaseModel
    for name in list(sys.modules):
        if name == 'onprem' or name.startswith('onprem.'):
            monkeypatch.delitem(sys.modules, name)
    from onprem.llm import LLM
    from zotero_summarizer.services import _adapters
    monkeypatch.setattr(_adapters, '_load_onprem', lambda: (LLM, None))
    class Reply(BaseModel):
        accepted: bool
    provider = ProviderConfig(name='none', base_url='https://example.invalid/exact', auth_mode='none',
                              api_key_env='UNUSED', structured_output=True)
    client = factory.build_client_for_provider(provider, 'synthetic')
    try:
        assert client.pydantic_prompt('Nonprivate structured control', Reply).accepted is True
    finally:
        client.close()
    assert len(wire) == 1 and 'authorization' not in wire[0].headers
    assert not any('ambient' in value for value in wire[0].headers.values())


def test_owned_authenticated_native_catalog_drops_poison_even_for_safe_header_names(wire):
    assert llm_models.list_anthropic_models('approved-secret', 'https://example.invalid/proxy', owned=True) == ['synthetic']
    assert len(wire) == 1 and wire[0].headers['x-api-key'] == 'approved-secret'
    assert not any('ambient' in value for value in wire[0].headers.values())
    assert wire[0].headers['anthropic-version'] == '2023-06-01'
    assert 'anthropic-beta' not in wire[0].headers


def test_owned_catalog_refuses_redirect_without_second_send(monkeypatch):
    seen = []
    def redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={'location': 'https://unapproved.invalid/steal'})
    original = llm_transport.owned_http_client
    monkeypatch.setattr(llm_transport, 'owned_http_client', lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(redirect)))
    with pytest.raises(llm_models.ModelListError):
        llm_models.list_openai_models('https://example.invalid/proxy', 'approved-secret', owned=True)
    assert len(seen) == 1
    assert seen[0].headers['authorization'] == 'Bearer approved-secret'
