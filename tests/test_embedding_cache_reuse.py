"""G1 regression: the gate predict path must REUSE the process-wide EmbeddingCache
(the runtime singleton) instead of constructing a fresh one per `predict()` batch.

A fresh instance has instance-local model memoization, so it reloaded the MiniLM
weights once per 50-item batch — the 178×-reload perf bug observed during a whole-
library Rescore. `_resolve_embedding_cache` returns the singleton when the db_path +
model match, and only constructs a fresh one when there's no runtime (training /
tests) or the model was swapped.
"""
from __future__ import annotations

import types
from pathlib import Path

import zotero_summarizer.services._common as common
from zotero_summarizer.services.model import classifier_features
from zotero_summarizer.storage.corpus import EmbeddingCache


def test_reuses_runtime_singleton_when_db_and_model_match(monkeypatch):
    shared = types.SimpleNamespace(db_path=Path("/x/corpus.db"), model_name="minilm")
    monkeypatch.setattr(common, "state", lambda: types.SimpleNamespace(embedding_cache=shared))
    got = classifier_features._resolve_embedding_cache(Path("/x/corpus.db"), "minilm")
    assert got is shared  # SAME object → MiniLM loads once, not per batch


def test_constructs_fresh_when_model_swapped(monkeypatch, tmp_path):
    # A model swap means the singleton is stale for this (db, model) key → build new.
    shared = types.SimpleNamespace(db_path=tmp_path / "corpus.db", model_name="OLD-MODEL")
    monkeypatch.setattr(common, "state", lambda: types.SimpleNamespace(embedding_cache=shared))
    got = classifier_features._resolve_embedding_cache(tmp_path / "corpus.db", "NEW-MODEL")
    assert got is not shared and isinstance(got, EmbeddingCache)


def test_constructs_fresh_when_no_runtime_singleton(monkeypatch, tmp_path):
    # Training / tests have no wired runtime embedding_cache → fall back to a fresh one.
    monkeypatch.setattr(common, "state", lambda: types.SimpleNamespace(embedding_cache=None))
    got = classifier_features._resolve_embedding_cache(tmp_path / "corpus.db", "minilm")
    assert isinstance(got, EmbeddingCache)


if __name__ == "__main__":  # ponytail: runnable check without pytest
    import tempfile

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    with tempfile.TemporaryDirectory() as d:
        test_reuses_runtime_singleton_when_db_and_model_match(_MP())
        test_constructs_fresh_when_model_swapped(_MP(), Path(d))
        test_constructs_fresh_when_no_runtime_singleton(_MP(), Path(d))
    print("ok")
