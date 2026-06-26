"""Offline mode: the ZS_OFFLINE → HF-env switch, prefetch --check cache report,
and the SPECTER2 friendly-error guidance."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import zotero_summarizer.cli as cli
import zotero_summarizer.settings as settings_mod


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    # Isolate from the repo's real .env + any inherited HF/ZS vars.
    monkeypatch.setattr(settings_mod, "default_project_root", lambda: tmp_path)
    for k in ("ZS_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_zs_offline_sets_hf_env(monkeypatch, clean_env):
    monkeypatch.setenv("ZS_OFFLINE", "1")
    assert cli.apply_offline_env() is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_default_stays_online(clean_env):
    assert cli.apply_offline_env() is False
    assert "HF_HUB_OFFLINE" not in os.environ
    assert "TRANSFORMERS_OFFLINE" not in os.environ


def test_respects_preset_hf_offline(monkeypatch, clean_env):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert cli.apply_offline_env() is True
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


@pytest.mark.parametrize("val", ["1", "true", "YES", "on", "True"])
def test_zs_offline_truthy_variants(monkeypatch, clean_env, val):
    monkeypatch.setenv("ZS_OFFLINE", val)
    assert cli.apply_offline_env() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
def test_zs_offline_falsy_variants(monkeypatch, clean_env, val):
    monkeypatch.setenv("ZS_OFFLINE", val)
    assert cli.apply_offline_env() is False


# --- prefetch --check cache report ---

def test_cache_report(monkeypatch):
    from zotero_summarizer.cli import _app

    class _Repo:
        def __init__(self, rid, size):
            self.repo_id = rid
            self.size_on_disk = size

    monkeypatch.setattr(
        "huggingface_hub.scan_cache_dir",
        lambda: SimpleNamespace(repos=[_Repo("allenai/specter2_base", 400_000_000)]),
    )
    report = _app._cache_report([("gate", "allenai/specter2_base"), ("rerank", "BAAI/bge-reranker-v2-m3")])
    by = {r["repo_id"]: r for r in report}
    assert by["allenai/specter2_base"]["cached"] is True
    assert by["allenai/specter2_base"]["size_mb"] == 400.0
    assert by["BAAI/bge-reranker-v2-m3"]["cached"] is False
    assert by["BAAI/bge-reranker-v2-m3"]["size_mb"] == 0.0


def test_model_targets_includes_four(monkeypatch):
    from zotero_summarizer.cli import _app
    config = SimpleNamespace(
        corpus=SimpleNamespace(
            embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            reranker_model="BAAI/bge-reranker-v2-m3",
        ),
        # _model_targets reads quality_review.shadow_claim_check to decide whether to
        # add the optional MiniCheck encoder target — off → exactly the four below.
        quality_review=SimpleNamespace(shadow_claim_check=False),
    )
    repos = [r for _, r in _app._model_targets(config)]
    assert "allenai/specter2_base" in repos
    assert "allenai/specter2" in repos
    assert "sentence-transformers/all-MiniLM-L6-v2" in repos
    assert "BAAI/bge-reranker-v2-m3" in repos


# --- SPECTER2 friendly offline error (re-raised, not swallowed) ---

def test_specter2_offline_error_is_actionable(monkeypatch):
    import transformers
    import zotero_summarizer.services.model.classifier_embed as ce

    ce._MODEL_CACHE.pop("loaded", None)

    def _boom(*a, **k):
        raise OSError("Can't reach huggingface.co (offline)")

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", _boom)
    with pytest.raises(RuntimeError, match="prefetch-models"):
        ce._load_specter2()
