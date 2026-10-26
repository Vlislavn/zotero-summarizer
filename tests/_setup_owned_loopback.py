"""Parent-only fixture for one exact owned TCP endpoint, never resident services."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
import json
import os
import socket
import sys
from threading import Event, Thread
from types import SimpleNamespace

from tests import setup_request_transport_bootstrap as bootstrap
from tests.test_setup_load_admission16 import bind_configuration, known_observation, policy_for, write_policy
from tests.test_setup_load_ownership16 import routing
from zotero_summarizer.models.providers import ProviderType, resolve_stage
from zotero_summarizer.services import _adapters
from zotero_summarizer.services.llm import factory, operational_check as oc
from zotero_summarizer.services.setup import load_admission


def _sdk_constructor(tmp_path, monkeypatch, kind, options):
    if kind == "anthropic":
        import anthropic
        native = anthropic.Anthropic

        def construct(**kwargs):
            options.append(kwargs)
            return native(**kwargs)

        monkeypatch.setattr(anthropic, "Anthropic", construct)
        return
    from onprem.llm import LLM
    from langchain_openai import ChatOpenAI
    from openai import OpenAI

    def construct(**kwargs):
        options.append(kwargs)
        inner = LLM(**kwargs, model_download_path=str(tmp_path / "models"), vectordb_path=str(tmp_path / "vectors"))
        assert type(inner.llm) is ChatOpenAI and type(inner.llm.root_client) is OpenAI
        return inner

    monkeypatch.setattr(_adapters, "_load_onprem", lambda: (construct, None))


def _response_body(kind):
    if kind == "anthropic":
        return json.dumps({"id": "msg_fixture", "type": "message", "role": "assistant", "model": "synthetic",
            "stop_reason": "end_turn", "stop_sequence": None, "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
    from tests.test_setup_request_transport import completion
    return completion("ok").content


@contextmanager
def owned_sdk_wire(tmp_path, monkeypatch, mode, *, kind="openai"):
    permit = sys._zs_setup_owned_loopback
    budget = min(bootstrap._timeout_seconds(os.getenv(bootstrap.TIMEOUT_ENV)), oc._LOCAL_PROBE_TIMEOUT_SECS)
    stopping, received, release, errors, thread = Event(), Event(), Event(), [], None
    listener = permit.open()
    try:
        listener.settimeout(budget)
        monkeypatch.setattr(socket, "getaddrinfo", permit.resolve)
        monkeypatch.setattr(socket.socket, "connect", lambda sock, address: permit.connect(sock, address))
        routes = routing()
        routes.providers[0].type = ProviderType(kind)
        routes.providers[0].base_url = f"http://127.0.0.1:{permit.address[1]}" + ("/v1" if kind == "openai" else "")
        resolved = resolve_stage(routes, "feed")
        configuration = write_policy(tmp_path, policy_for(resolved))
        request_budget = bootstrap.timing_seconds(os.getenv(bootstrap.TIMING_ENV)) if mode == "sdk_timeout" else budget
        configuration = bind_configuration(replace(configuration, summary_timeout_seconds=request_budget))
        monkeypatch.setattr(load_admission, "_observe_executing_runtime", lambda *_a: known_observation(resolved))
        monkeypatch.setattr(factory, "resolve_api_key", lambda _: "synthetic-test-key")
        options, observations = [], []
        _sdk_constructor(tmp_path, monkeypatch, kind, options)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observations.append((self.command, self.path, payload["model"]))
                received.set()
                if mode == "disconnect":
                    self.close_connection = True
                    return
                if mode in {"waiter_late", "sdk_timeout"}:
                    assert release.wait(budget), "Owned server release work was not completed"
                body = _response_body(kind)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def serve():
            try:
                connection, address = listener.accept()
                with connection:
                    connection.settimeout(budget)
                    Handler(connection, address, SimpleNamespace())
            except BaseException as exc:
                if not stopping.is_set():
                    errors.append(exc)

        thread = Thread(target=serve, name="owned-setup-sdk-http")
        thread.start()
        yield SimpleNamespace(provider=resolved.provider, model=resolved.model, settings=configuration,
                              observations=observations, options=options, budget=budget, permit=permit,
                              received=received, release=release)
    finally:
        stopping.set()
        release.set()
        permit.close()
        if thread is not None and thread.ident is not None:
            thread.join(timeout=budget)
            assert not thread.is_alive(), "Exact owned HTTP worker must be reaped"
        assert not errors, errors
