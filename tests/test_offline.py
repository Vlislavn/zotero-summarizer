"""Offline environment, cache reporting, and actionable model errors."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import zotero_summarizer.cli as cli
import zotero_summarizer.settings as settings_mod


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "default_project_root", lambda: tmp_path)
    for k in ("ZS_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        monkeypatch.delenv(k, raising=False)
    yield tmp_path
    for k in ("ZS_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ.pop(k, None)


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
    from zotero_summarizer.services.setup.assets import cache_report

    class _Repo:
        def __init__(self, rid, size):
            self.repo_id = rid
            self.size_on_disk = size
            self.revisions = []

    monkeypatch.setattr(
        "huggingface_hub.scan_cache_dir",
        lambda: SimpleNamespace(repos=[_Repo("allenai/specter2_base", 400_000_000)]),
    )
    report = cache_report([("gate", "allenai/specter2_base", None), ("rerank", "BAAI/bge-reranker-v2-m3", None)])
    by = {r["repo_id"]: r for r in report}
    assert by["allenai/specter2_base"]["cached"] is True
    assert by["allenai/specter2_base"]["size_mb"] == 400.0
    assert by["BAAI/bge-reranker-v2-m3"]["cached"] is False
    assert by["BAAI/bge-reranker-v2-m3"]["size_mb"] == 0.0


def test_model_targets_includes_four():
    from zotero_summarizer.services.setup.assets import model_targets
    config = SimpleNamespace(
        corpus=SimpleNamespace(
            embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            reranker_model="BAAI/bge-reranker-v2-m3",
        ),
        quality_review=SimpleNamespace(shadow_claim_check=False),
    )
    repos = [r for _, r, _ in model_targets(config)]
    assert "allenai/specter2_base" in repos
    assert "allenai/specter2" in repos
    assert "sentence-transformers/all-MiniLM-L6-v2" in repos
    assert "BAAI/bge-reranker-v2-m3" in repos


@pytest.mark.parametrize(("name", "value"), [("ZS_OFFLINE", "true"), ("HF_HUB_OFFLINE", "1")])
def test_strict_offline_blocks_uncached_pdf_and_rss_network(monkeypatch, tmp_path, name, value):
    from zotero_summarizer.integrations.app_rss import AppRssReader
    from zotero_summarizer.integrations.pdf_fetch import fetch_pdf
    from zotero_summarizer.services.library import _pdf_acquire

    monkeypatch.setenv(name, value)
    assert fetch_pdf("https://example.com/paper.pdf", cache_dir=tmp_path) is None
    assert AppRssReader(tmp_path / "missing.db").refresh_feeds()["offline"] is True
    bomb = SimpleNamespace(fetch_work_by_doi=lambda *_: pytest.fail("OpenAlex network attempted"))
    app = SimpleNamespace(unpaywall_client=bomb, openalex_cache=None, openalex_client=bomb)
    assert _pdf_acquire._headless_sources(app, SimpleNamespace(prestige=SimpleNamespace(user_agent_email="")), "https://publisher.test/p", "10.1/x", "") == []


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


@pytest.mark.parametrize(("name", "value"), [
    ("ZS_OFFLINE", "1"), ("ZS_OFFLINE", "true"), ("ZS_OFFLINE", " YES "),
    ("ZS_OFFLINE", "on"), ("ZS_OFFLINE", "True"), ("HF_HUB_OFFLINE", "1"),
])
def test_prefetch_offline_refuses_before_configuration_or_acquisition(monkeypatch, clean_env, name, value):
    from zotero_summarizer.services.setup import assets

    flags = ("ZS_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")
    for key in flags[1:]:
        monkeypatch.setenv(key, "retained")
    monkeypatch.setenv(name, value)
    before = {key: os.environ.get(key) for key in flags}

    def unexpected(*_args, **_kwargs):
        pytest.fail("offline prefetch reached configuration, acquisition or report work")

    for operation in ("read_config", "_load_assets", "asset_report"):
        monkeypatch.setattr(assets, operation, unexpected)
    settings = SimpleNamespace(config_path=clean_env / "goals.yaml", calibration_path=clean_env / "data/calibration.json")
    with pytest.raises(RuntimeError, match="offline"):
        assets.prefetch_assets(settings)
    assert {key: os.environ.get(key) for key in flags} == before


def test_online_prefetch_preserves_the_existing_acquisition_path(monkeypatch, clean_env):
    from zotero_summarizer.services.setup import assets

    flags = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")
    for key in flags:
        monkeypatch.setenv(key, "0")
    config, report, loaded = object(), {"offline_ready": True, "models": []}, []
    settings = SimpleNamespace(config_path=clean_env / "goals.yaml", calibration_path=clean_env / "data/calibration.json")

    def acquire(actual_settings, actual_config):
        assert actual_settings is settings and actual_config is config
        assert not any(key in os.environ for key in flags)
        loaded.append(actual_settings)

    monkeypatch.setattr(assets, "read_config", lambda *_: config)
    monkeypatch.setattr(assets, "_load_assets", acquire)
    monkeypatch.setattr(assets, "asset_report", lambda actual: report if actual is settings else pytest.fail("wrong settings"))
    assert assets.prefetch_assets(settings) is report
    assert loaded == [settings]


@pytest.mark.parametrize("check", [False, True])
def test_offline_prefetch_dispatch_keeps_check_nonloading(monkeypatch, clean_env, capsys, check):
    import json
    from zotero_summarizer.cli import _app
    from zotero_summarizer.services.setup import assets

    monkeypatch.setenv("ZS_OFFLINE", "true")
    settings = SimpleNamespace(config_path=clean_env / "goals.yaml", calibration_path=clean_env / "data/calibration.json")
    report = {"offline_ready": False, "models": []}
    monkeypatch.setattr(_app.Settings, "load", lambda **_: settings)

    def unexpected(*_args, **_kwargs):
        pytest.fail("offline dispatcher reached configuration or acquisition")

    monkeypatch.setattr(assets, "read_config", unexpected)
    monkeypatch.setattr(assets, "_load_assets", unexpected)
    monkeypatch.setattr(assets, "asset_report", lambda actual: report if actual is settings else pytest.fail("wrong settings"))
    args = SimpleNamespace(project_root=clean_env, check=check)
    if check:
        assert _app._prefetch_models(args) == 0
        assert json.loads(capsys.readouterr().out) == report
    else:
        with pytest.raises(RuntimeError, match="offline"):
            _app._prefetch_models(args)
