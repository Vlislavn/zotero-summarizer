import builtins
from types import SimpleNamespace
import sys

import pytest

from zotero_summarizer.services import _adapters


def test_missing_dependency_does_not_probe_an_implicit_source_checkout(monkeypatch):
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name.startswith('onprem'):
            raise ImportError('dependency not installed')
        return original_import(name, *args, **kwargs)

    monkeypatch.delenv('ONPREM_PATH', raising=False)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    before = list(sys.path)
    with pytest.raises(ImportError, match='dependency not installed'):
        _adapters._load_onprem()
    assert sys.path == before


def test_explicit_source_checkout_is_respected(monkeypatch, tmp_path):
    original_import = builtins.__import__
    calls = 0
    sentinel = object()

    def source_import(name, *args, **kwargs):
        nonlocal calls
        if name == 'onprem.llm':
            calls += 1
            if calls == 1:
                raise ImportError('installed package unavailable')
            return SimpleNamespace(LLM=sentinel)
        if name == 'onprem.ingest.base':
            return SimpleNamespace(load_single_document=sentinel)
        return original_import(name, *args, **kwargs)

    monkeypatch.setenv('ONPREM_PATH', str(tmp_path))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(builtins, '__import__', source_import)
    assert _adapters._load_onprem() == (sentinel, sentinel)
    assert sys.path[0] == str(tmp_path)
