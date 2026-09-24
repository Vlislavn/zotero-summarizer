"""A run ID must never mix generation identities."""
import json
from unittest.mock import Mock

import pytest

from zotero_summarizer.services.faithbench._runner import RunInputs, RunOptions, RunPaths, run_benchmark, write_or_check_manifest
from tests.test_faithbench_runner import _benchmark, _fake_config, _generation_models, _prepare_run


@pytest.mark.parametrize("field,replacement", [
    ("provider_name", "other"), ("base_url", "https://other.test/v1"),
    ("research_goals", ["other goal"]), ("generation_sha256", "b" * 64),
    ("benchmark_content_sha256", "b" * 64),
])
def test_changed_generation_cannot_reuse_a_run_directory(tmp_path, field, replacement):
    paths = RunPaths(tmp_path)
    manifest = {
        "run_id": "run", "model": "model", "provider_name": "provider",
        "base_url": "https://model.test/v1", "benchmark_sha256": "a" * 64,
        "benchmark_content_sha256": "a" * 64, "generation_sha256": "a" * 64,
        "benchmark_review_sha256": "a" * 64,
        "research_goals": ["clinical AI"], "conditions": ["full_text"],
        "tracks": ["qa"], "runs": 1, "limit": None,
    }
    write_or_check_manifest(paths, manifest)
    before = paths.manifest.read_bytes()
    paths.responses.write_text('{"interrupted":')
    with pytest.raises(RuntimeError, match="resume refused"):
        write_or_check_manifest(paths, {**manifest, field: replacement})
    assert paths.manifest.read_bytes() == before
    assert paths.responses.read_text() == '{"interrupted":'


def test_legacy_manifest_without_generation_identity_is_not_silently_upgraded(tmp_path):
    paths = RunPaths(tmp_path)
    legacy = {"model": "model", "benchmark_sha256": "a" * 64}
    paths.manifest.write_text(json.dumps(legacy))
    before = paths.manifest.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match="identity|resume refused"):
        write_or_check_manifest(paths, legacy)
    assert paths.manifest.read_bytes() == before


@pytest.mark.parametrize("change", ["goals", "digest_prompt", "max_chars", "endpoint", "model",
                                   "decomposer", "decomposer_endpoint", "qa_prompt", "schema", "benchmark"])
def test_direct_runner_refuses_changed_live_inputs_before_repair_or_models(tmp_path, monkeypatch, change):
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.faithbench import _runner

    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(tmp_path / "run")
    inputs = RunInputs(meta, items, papers_dir, paths)
    options = RunOptions(conditions=("full_text",), tracks=("qa", "claims"))
    config, models = _fake_config(), _generation_models(True)
    _prepare_run(inputs, options, config=config, models=models)
    paths.responses.write_text('{"interrupted":')
    before = {path: path.read_bytes() for path in paths.run_dir.iterdir()}
    if change == "goals":
        config.research_goals = ["a different research question"]
    elif change == "digest_prompt":
        config.prompts.paper_digest = "Changed digest instructions {full_text}"
    elif change == "max_chars":
        config.quality_review.max_text_chars = 1000
    elif change in {"endpoint", "decomposer_endpoint"}:
        index = int(change == "decomposer_endpoint")
        updated = models[index].model_copy(update={"provider": models[index].provider.model_copy(
            update={"base_url": "https://changed.test/v1"})})
        models = (models[0], updated) if index else (updated, models[1])
    elif change in {"model", "decomposer"}:
        index = int(change == "decomposer")
        updated = models[index].model_copy(update={"model": "changed"})
        models = (models[0], updated) if index else (updated, models[1])
    elif change == "qa_prompt":
        monkeypatch.setattr(_runner, "ANSWER_PROMPT", "Changed QA prompt")
    elif change == "schema":
        monkeypatch.setattr(PaperDigest, "model_json_schema", lambda: {"changed": True})
    else:
        inputs = RunInputs(meta.model_copy(update={"builder_model": "changed"}), items, papers_dir, paths)
    llm, decomposer = Mock(), Mock()
    with pytest.raises(RuntimeError, match="resume refused"):
        run_benchmark(run_id="r1", inputs=inputs, config=config, generation_models=models,
                      llm=llm, decompose_llm=decomposer, options=options)
    assert not llm.mock_calls and not decomposer.mock_calls
    assert {path: path.read_bytes() for path in paths.run_dir.iterdir()} == before


def test_cli_refuses_endpoint_change_before_constructing_clients(tmp_path, monkeypatch):
    from zotero_summarizer.cli import main, _faithbench
    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.setup import bootstrap
    from zotero_summarizer.settings import Settings

    settings = Settings.load(project_root=tmp_path)
    bootstrap.bootstrap_phase0(settings)
    monkeypatch.setattr(_common, "settings", lambda: settings)
    config = _common.read_config(settings.config_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    meta, items, papers_dir = _benchmark(settings.faithbench_dir)
    paths = RunPaths(settings.faithbench_dir / "runs" / "identity")
    options = RunOptions(tracks=("qa",), conditions=("full_text",), serial=resolved.provider.is_local,
                         max_workers=settings.triage_job_concurrency)
    manifest = _prepare_run(RunInputs(meta, items, papers_dir, paths), options,
                            run_id="identity", config=config, models=(resolved, None))
    before = {path: path.read_bytes() for path in paths.run_dir.iterdir()}
    changed = config.model_copy(deep=True)
    changed.llm_routing.provider_by_name(resolved.provider.name).base_url = "https://changed.test/v1"
    monkeypatch.setattr(_common, "read_config", lambda _: changed)
    build, remote = Mock(), Mock()
    monkeypatch.setattr(factory, "build_client_for_stage", build)
    monkeypatch.setattr(_faithbench, "_remote_llm", remote)
    with pytest.raises(RuntimeError, match="resume refused"):
        main(["faithbench", "run", "--project-root", str(tmp_path), "--run-id", "identity",
              "--benchmark", manifest["benchmark_path"], "--tracks", "qa", "--conditions", "full_text"])
    assert not build.mock_calls and not remote.mock_calls
    assert {path: path.read_bytes() for path in paths.run_dir.iterdir()} == before
