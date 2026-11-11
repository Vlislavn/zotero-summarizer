"""Canonical HTTPX authority spelling must not weaken exact destination ownership."""
import httpx
import pytest
from zotero_summarizer.integrations.llm_transport import owned_http_client


@pytest.mark.parametrize('base', ['http://127.0.0.1:11434/exact/proxy',
    'https://EXAMPLE.invalid/exact/proxy', 'https://example.invalid:443/exact/proxy',
    'https://example.invalid/exact/%2Fproxy'])
def test_equivalent_authority_keeps_exact_proxy_path_and_no_auth(base):
    sent = []
    def respond(request):
        sent.append(request)
        return httpx.Response(200)
    with owned_http_client(base, None, timeout=1, transport=httpx.MockTransport(respond)) as client:
        assert client.get(base + '/models', headers={'authorization': 'synthetic poison'}).status_code == 200
    assert len(sent) == 1
    assert sent[0].url.raw_path == httpx.URL(base + '/models').raw_path
    assert 'authorization' not in sent[0].headers and 'x-api-key' not in sent[0].headers


@pytest.mark.parametrize('target', ['https://other.invalid/exact/proxy/models',
    'https://example.invalid:444/exact/proxy/models', 'http://example.invalid/exact/proxy/models',
    'https://example.invalid/exact/proxy-escape/models', 'https://example.invalid/exact/other/models',
    'https://example.invalid/exact/proxy/../escape', 'https://user:password@example.invalid/exact/proxy/models'])
def test_different_origin_port_path_or_userinfo_refuses_before_send(target):
    with owned_http_client('https://EXAMPLE.invalid:443/exact/proxy', None, timeout=1,
            transport=httpx.MockTransport(lambda _: pytest.fail('unapproved destination sent'))) as client:
        with pytest.raises(ValueError, match='approved API destination'):
            client.get(target)
