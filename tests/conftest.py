from __future__ import annotations

import sys
import types
import os
import socket
from tempfile import TemporaryDirectory
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_onprem_stubs() -> None:
    if "onprem.llm" in sys.modules and "onprem.ingest.base" in sys.modules:
        return

    onprem_module = sys.modules.setdefault("onprem", types.ModuleType("onprem"))
    llm_module = types.ModuleType("onprem.llm")
    ingest_module = types.ModuleType("onprem.ingest")
    ingest_base_module = types.ModuleType("onprem.ingest.base")

    class DummyLLM:
        def __init__(self, *args, **kwargs):
            # Capture kwargs so tests can introspect model name etc.
            self.model = kwargs.get("model", args[1] if len(args) > 1 else None)
            self._kwargs = kwargs

        def prompt(self, *args, **kwargs):
            raise NotImplementedError("DummyLLM.prompt should not be called in unit tests")

        def pydantic_prompt(self, *args, **kwargs):
            raise NotImplementedError("DummyLLM.pydantic_prompt should not be called in unit tests")

    def dummy_load_single_document(*args, **kwargs):
        return []

    llm_module.LLM = DummyLLM
    ingest_base_module.load_single_document = dummy_load_single_document
    ingest_module.base = ingest_base_module
    onprem_module.llm = llm_module
    onprem_module.ingest = ingest_module

    sys.modules["onprem.llm"] = llm_module
    sys.modules["onprem.ingest"] = ingest_module
    sys.modules["onprem.ingest.base"] = ingest_base_module


_install_onprem_stubs()


def pytest_configure(config):
    # CLI imports inspect .env during collection, before any fixture runs.
    directory = TemporaryDirectory(prefix="zs-test-collection-")
    patch = pytest.MonkeyPatch()
    patch.setenv("ZOTERO_SUMMARIZER_HOME", directory.name)
    config.add_cleanup(directory.cleanup)
    config.add_cleanup(patch.undo)


@pytest.fixture(autouse=True)
def _isolate_app_and_network(monkeypatch, tmp_path):
    import keyring
    from zotero_summarizer import runtime

    monkeypatch.setenv("ZOTERO_SUMMARIZER_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "_context", None)
    for name in os.environ:
        if name.endswith(("_API_KEY", "_TOKEN")):
            monkeypatch.delenv(name)
    monkeypatch.setattr(keyring, "get_password", lambda _service, _name: None)

    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Tests must mock network connections")
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Tests must mock network connections")
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture(autouse=True)
def _stub_corpus_encoder(monkeypatch):
    from zotero_summarizer.storage import corpus

    class StubEncoder:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, text, *, normalize_embeddings):
            return [1.0, 0.0, 0.0]

    monkeypatch.setattr(corpus, "SentenceTransformer", StubEncoder)
