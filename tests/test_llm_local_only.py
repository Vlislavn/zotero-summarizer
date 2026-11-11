"""Owned HTTP origin only: local-only factory never follows redirects/proxies."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import socket

_ORIGINAL_CONNECT = socket.socket.connect
from unittest.mock import Mock

import pytest

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services import _adapters
from zotero_summarizer.services.llm import factory


@contextmanager
def origin():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(307)
            self.send_header("Location", "https://outside.invalid/forbidden")
            self.end_headers()
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)


@pytest.mark.parametrize("url", ["https://remote.invalid/v1", "http://192.168.1.2/v1", "http://0.0.0.0/v1",
    "http://localhost.remote.invalid/v1", "http://127.0.0.1@remote.invalid/v1", "http://localhost/model.gguf"])
def test_refusal_before_credentials_or_client_construction(url, monkeypatch):
    load = Mock(side_effect=AssertionError("client construction forbidden"))
    key = Mock(side_effect=AssertionError("credentials forbidden"))
    monkeypatch.setattr(_adapters, "_load_onprem", load)
    monkeypatch.setattr(factory, "resolve_api_key", key)
    with pytest.raises(ValueError, match="loopback"):
        factory.build_client_for_provider(ProviderConfig(name="local", base_url=url, api_key_env="LOCAL"), "fixture", local_only=True)
    load.assert_not_called(); key.assert_not_called()


def test_loopback_transport_blocks_redirect_proxy_and_each_remote_send_without_model(monkeypatch):
    class FakeLLM:
        def __init__(self, **kwargs):
            self.options = kwargs
        def prompt(self, prompt, **kwargs):
            return self.options["http_client"].get(prompt)
    monkeypatch.setattr(_adapters, "_load_onprem", lambda: (FakeLLM, None))
    monkeypatch.setattr(factory, "resolve_api_key", lambda _: "local")
    # An ambient proxy cannot receive any source payload, even for localhost.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with origin() as (url, calls):
        port = int(url.split(':')[2].split('/')[0])
        def owned_connect(sock, address):
            assert address == ('127.0.0.1', port), 'only the owned synthetic origin is allowed'
            return _ORIGINAL_CONNECT(sock, address)
        monkeypatch.setattr(socket.socket, 'connect', owned_connect)
        client = factory.build_client_for_provider(ProviderConfig(name="local", base_url=url, api_key_env="LOCAL"), "fixture", local_only=True)
        transport = client._owned_transport
        assert client._inner.options["max_retries"] == 0 and client._inner.options["cache"] is False
        assert client.prompt(url).status_code == 307
        assert calls == ["/v1"]
        with pytest.raises(ValueError, match="loopback"):
            client.prompt("https://outside.invalid/forbidden")
        assert calls == ["/v1"]
        client.close()
        assert transport.is_closed


def test_setup_verification_cannot_be_silently_combined_with_local_only(monkeypatch):
    load = Mock(side_effect=AssertionError("no construction"))
    monkeypatch.setattr(_adapters, "_load_onprem", load)
    with pytest.raises(ValueError):
        factory.build_client_for_provider(ProviderConfig(name="local", base_url="http://localhost:11434/v1", api_key_env="LOCAL"), "fixture", local_only=True, verification=True)
    load.assert_not_called()
