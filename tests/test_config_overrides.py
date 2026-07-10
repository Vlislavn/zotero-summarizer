"""Phase 1: intent-only persistence + ZS_* env override layer.

Covers ``services/_common.write_user_config`` (goals.yaml stays intent-only) and
``services/config_overrides`` (env overrides win over the file, re-validate, and the
generated ``docs/overrides.md`` never drifts from the registry).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from zotero_summarizer.models.config import USER_OWNED_KEYS
from zotero_summarizer.services._common import read_config, write_user_config
from zotero_summarizer.services.config_overrides import (
    REGISTRY,
    apply_env_overrides,
    render_overrides_doc,
)
from zotero_summarizer.services.setup.bootstrap import _default_goals_config

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_write_user_config_writes_only_user_owned_keys(tmp_path: Path) -> None:
    path = tmp_path / "goals.yaml"
    write_user_config(path, _default_goals_config())
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(written) <= USER_OWNED_KEYS
    # The file must reload as a standalone valid GoalsConfig.
    for required in ("research_goals", "relevance_scale", "llm"):
        assert required in written


def test_write_user_config_omits_default_university_access(tmp_path: Path) -> None:
    path = tmp_path / "goals.yaml"
    write_user_config(path, _default_goals_config())
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    # university_access is off by default → not written (no clutter).
    assert "university_access" not in written


def test_round_trip_preserves_effective_config(tmp_path: Path, monkeypatch) -> None:
    # No ZS_* set → read_config's env step is a no-op; the stripped file must
    # reload to the SAME effective config (technical sections fall back to the
    # validated code defaults, which now match — Gotcha #1 default flips).
    for ov in REGISTRY:
        monkeypatch.delenv(ov.env, raising=False)
    cfg = _default_goals_config()
    path = tmp_path / "goals.yaml"
    write_user_config(path, cfg)
    reloaded = read_config(path)
    # read_config auto-derives num_ctx for LOCAL providers (a load-time safety enrichment,
    # like env/calibration application) so ollama doesn't truncate long prompts. Mirror it
    # on the expected side so the round-trip still asserts intent-only persistence.
    from zotero_summarizer.services._common import _derive_local_num_ctx
    assert reloaded.model_dump(mode="json") == _derive_local_num_ctx(cfg).model_dump(mode="json")


def test_prestige_enabled_by_default(tmp_path: Path, monkeypatch) -> None:
    # Regression: prestige was default-OFF, so OpenAlex citation enrichment never
    # ran and every library item read as "new" prestige (high/low buckets empty).
    # It is now a validated code default — a stripped intent-only goals.yaml must
    # reload with prestige on.
    for ov in REGISTRY:
        monkeypatch.delenv(ov.env, raising=False)
    monkeypatch.delenv("ZS_OFFLINE", raising=False)
    path = tmp_path / "goals.yaml"
    write_user_config(path, _default_goals_config())
    assert read_config(path).prestige.enabled is True


def test_zs_offline_forces_prestige_off(tmp_path: Path, monkeypatch) -> None:
    # Air-gap contract: ZS_OFFLINE wins even over an explicit ZS_PRESTIGE_ENABLED=1,
    # so an offline run never reaches api.openalex.org.
    monkeypatch.setenv("ZS_OFFLINE", "1")
    monkeypatch.setenv("ZS_PRESTIGE_ENABLED", "1")
    path = tmp_path / "goals.yaml"
    write_user_config(path, _default_goals_config())
    assert read_config(path).prestige.enabled is False


def test_env_override_beats_file_and_revalidates(monkeypatch) -> None:
    monkeypatch.setenv("ZS_CORPUS_SIMILARITY_THRESHOLD", "-0.5")
    monkeypatch.setenv("ZS_CLASSIFIER_GATE_MODEL_NAME", "tabpfn")
    out = apply_env_overrides(_default_goals_config())
    assert out.corpus.similarity_threshold == -0.5
    assert out.classifier_gate.model_name == "tabpfn"


def test_env_override_fails_loud_on_bad_value(monkeypatch) -> None:
    monkeypatch.setenv("ZS_CLASSIFIER_GATE_MODEL_NAME", "bogus")
    with pytest.raises(ValidationError):
        apply_env_overrides(_default_goals_config())


def test_legacy_env_aliases_preserved() -> None:
    names = {ov.env for ov in REGISTRY}
    assert "ZS_QUALITY_BAND_PRIMARY" in names
    assert "ZS_DEEP_REVIEW_PREWARM_K" in names


def test_user_owned_keys_have_no_env_override() -> None:
    # The escape hatch covers ONLY system-owned knobs; intent/connection stay
    # file-authored (env-overriding research goals would be a category error).
    for ov in REGISTRY:
        assert ov.path.split(".", 1)[0] not in USER_OWNED_KEYS


def test_overrides_doc_matches_registry() -> None:
    committed = (_REPO_ROOT / "docs" / "overrides.md").read_text(encoding="utf-8")
    assert committed == render_overrides_doc(), (
        "docs/overrides.md is stale — run `python tools/gen_overrides_doc.py`"
    )


def test_derive_num_ctx_fits_deep_review_prompt() -> None:
    """num_ctx must fit the largest deep-review prompt: text budget (chars/4) + output
    tokens + headroom. ollama's own ~2–4k default silently truncates a 60k-char prompt;
    the derived value must clear it with room for the response + thinking."""
    from zotero_summarizer.services._common import derive_num_ctx

    # 60k chars / 4 = 15k prompt tokens + 4096 output * 1.25 headroom ≈ 23872
    n = derive_num_ctx(max_text_chars=60_000, max_tokens=4096)
    assert n == int((60_000 / 4 + 4096) * 1.25)
    assert n > 15_000  # clears the prompt alone
    assert n < 60_000  # doesn't wildly over-allocate KV-cache RAM
    with pytest.raises(ValueError):
        derive_num_ctx(max_text_chars=0, max_tokens=4096)


def test_read_config_auto_derives_num_ctx_for_local_providers(tmp_path: Path) -> None:
    """Regression: a LOCAL ollama provider left num_ctx unset → ollama's tiny ~2–4k
    default silently truncated long deep-review prompts. read_config must auto-derive a
    fitting num_ctx for local providers; remote providers stay None (they size their own)."""
    from zotero_summarizer.models.providers import ProviderConfig

    path = tmp_path / "goals.yaml"
    path.write_text(
        """
        research_goals: [cancer immunology]
        triage_criteria: [methodology]
        relevance_scale: {1: a, 2: b, 3: c, 4: d, 5: e}
        llm:
          draft_model: qwen3:8b
          refine_model: qwen3:8b
          api_base: http://127.0.0.1:11434/v1
          api_key_env: DUMMY
        llm_routing:
          providers:
            - name: local
              type: openai
              base_url: http://127.0.0.1:11434/v1
              api_key_env: DUMMY
            - name: api
              type: openai
              base_url: https://remote.example/v1
              api_key_env: DUMMY
          default: {provider: local, model: qwen3:8b}
          deep_review: {provider: api, model: GPT-OSS-120B}
        """,
        encoding="utf-8",
    )
    config = read_config(path)
    local = next(p for p in config.llm_routing.providers if p.name == "local")
    api = next(p for p in config.llm_routing.providers if p.name == "api")
    assert local.is_local and local.num_ctx is not None and local.num_ctx > 15_000
    assert not api.is_local and api.num_ctx is None  # remote sizes its own window
