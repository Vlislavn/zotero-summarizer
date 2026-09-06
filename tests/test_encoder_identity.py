"""Pinned weights must agree across loading, vector caches and model reuse."""
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from zotero_summarizer.services.model import classifier_const as const
from zotero_summarizer.services.model import classifier_embed as embed
from zotero_summarizer.services.model import classifier_inputs
from zotero_summarizer.services.setup import assets


def test_loader_pins_tokenizer_base_and_adapter(monkeypatch):
    model = Mock()
    tokenizer = Mock(return_value=object())
    factory = Mock(return_value=model)
    monkeypatch.setattr(embed, "_MODEL_CACHE", {})
    monkeypatch.setattr(embed, "_select_device", lambda torch: "cpu")
    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained", tokenizer)
    monkeypatch.setattr("adapters.AutoAdapterModel.from_pretrained", factory)
    embed._load_specter2()
    base_revision = factory.call_args.kwargs["revision"]
    assert re.fullmatch("[a-f0-9]{40}", base_revision)
    assert tokenizer.call_args.kwargs["revision"] == base_revision
    assert re.fullmatch("[a-f0-9]{40}", model.load_adapter.call_args.kwargs["version"])


@pytest.mark.parametrize("field", ["SPECTER2_MODEL_NAME", "SPECTER2_MODEL_REVISION", "SPECTER2_ADAPTER_REVISION"])
def test_embedding_key_includes_base_and_adapter_revisions(monkeypatch, field):
    before = embed._content_hash("Title", "Abstract")
    monkeypatch.setattr(const, field, "changed", raising=False)
    monkeypatch.setattr(embed, field, "changed", raising=False)
    assert embed._content_hash("Title", "Abstract") != before


@pytest.mark.parametrize("batch", [False, True])
def test_revision_change_recomputes_existing_vectors(tmp_path, monkeypatch, batch):
    calls = []

    def encode(pairs, **kwargs):
        calls.append(pairs)
        return np.full((len(pairs), const.EMBEDDING_DIM), len(calls), dtype=np.float32)

    monkeypatch.setattr(embed, "compute_embeddings_batch", encode)
    db = tmp_path / "corpus.db"

    def read():
        if batch:
            return embed.get_or_compute_embeddings_batch(db, [{"item_key": "A", "title": "T", "abstract": "A"}])
        return embed.get_or_compute_embedding(db, "A", "T", "A")

    assert np.all(read() == 1)
    assert np.all(read() == 1)
    monkeypatch.setattr(const, "SPECTER2_MODEL_REVISION", "changed", raising=False)
    monkeypatch.setattr(embed, "SPECTER2_MODEL_REVISION", "changed", raising=False)
    assert np.all(read() == 2)


@pytest.mark.parametrize("field", ["SPECTER2_MODEL_REVISION", "SPECTER2_ADAPTER_REVISION"])
def test_pins_are_explicit_training_inputs(tmp_path, monkeypatch, field):
    golden = tmp_path / "golden.csv"
    golden.write_text("item_key,title\nA,T\n")
    args = dict(classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
                goals_config=None, n_folds=5, pca_dim=100)
    before = classifier_inputs.load_training_inputs(golden, **args)
    monkeypatch.setattr(const, field, "changed", raising=False)
    assert classifier_inputs.load_training_inputs(golden, **args).sha256 != before.sha256


def test_cache_report_requires_the_requested_revision(monkeypatch):
    repo = SimpleNamespace(repo_id="allenai/specter2_base", size_on_disk=300,
                           revisions=[SimpleNamespace(commit_hash="old", size_on_disk=100),
                                      SimpleNamespace(commit_hash="pinned", size_on_disk=200)])
    monkeypatch.setattr("huggingface_hub.scan_cache_dir", lambda: SimpleNamespace(repos=[repo]))
    report = assets.cache_report([("gate", repo.repo_id, "missing"), ("gate", repo.repo_id, "pinned")])
    assert [row["cached"] for row in report] == [False, True]


def test_hash_has_unambiguous_content_fields():
    assert embed._content_hash("a|||b", "c") != embed._content_hash("a", "b|||c")


def test_setup_targets_share_loader_pins():
    config = SimpleNamespace(corpus=SimpleNamespace(embedding_model="corpus", reranker_model="reranker"),
                             quality_review=SimpleNamespace(shadow_claim_check=False))
    targets = assets.model_targets(config)
    assert targets[:2] == [
        ("gate encoder", const.SPECTER2_MODEL_NAME, const.SPECTER2_MODEL_REVISION),
        ("gate adapter", const.SPECTER2_ADAPTER_NAME, const.SPECTER2_ADAPTER_REVISION),
    ]


def test_installed_adapter_library_forwards_version_as_hub_revision(monkeypatch):
    from adapters import utils

    download = Mock(return_value="cached-snapshot")
    monkeypatch.setattr(utils, "snapshot_download", download)
    assert utils.pull_from_hf_model_hub(
        const.SPECTER2_ADAPTER_NAME, version=const.SPECTER2_ADAPTER_REVISION,
    ) == "cached-snapshot"
    assert download.call_args.kwargs["revision"] == const.SPECTER2_ADAPTER_REVISION


def test_cache_probe_distinguishes_absence_from_broken_schema(tmp_path):
    db = tmp_path / "corpus.db"
    assert embed._embedding_cached(db, "A", "hash") is False
    assert not db.exists()
    with sqlite3.connect(db) as conn:
        assert embed._embedding_cached(db, "A", "hash") is False
        conn.execute("CREATE TABLE specter2_embeddings (wrong_column TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        embed._embedding_cached(db, "A", "hash")
