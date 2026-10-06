"""Published benchmark entry points require caller-owned deployment selections."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_relevance_report_does_not_emit_static_dataset_claims(monkeypatch, tmp_path, capsys):
    from tools import bench_search_relevance as bench

    pool = tmp_path / 'pool.json'
    pool.write_text('[]')
    monkeypatch.setattr(bench, 'POOL', pool)
    monkeypatch.setattr(bench, '_load', lambda _path: [])
    observed = []
    monkeypatch.setattr(bench, 'report_for', lambda rows, judge: observed.append((rows, judge)))
    monkeypatch.setattr(sys, 'argv', ['bench_search_relevance.py', '--judges', 'caller-model'])
    assert bench.main() == 0
    assert observed == [([], 'caller-model')]
    assert capsys.readouterr().out == ''


def test_relevance_report_header_describes_only_the_loaded_run(monkeypatch, capsys):
    from types import SimpleNamespace
    from tools import bench_search_relevance as bench

    candidates = [SimpleNamespace(candidate_id='good', query_score=1.0, is_retracted=False),
                  SimpleNamespace(candidate_id='bad', query_score=0.0, is_retracted=False)]
    monkeypatch.setattr(bench, '_labels', lambda _judge: {'fixture': {'good': 3, 'bad': 0}})
    bench.report_for([{'query': 'fixture', 'cov': 'ml', 'candidates': candidates}], 'caller-model')
    output = capsys.readouterr().out
    assert output.splitlines()[2] == 'judge = caller-model  (score-only order)'
    assert 'ALL (n=1)' in output


def _cli(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / script), *args],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("script,missing", [
    ("bench_llm_ner.py", "--model"),
    ("bench_llm_params.py", "--model"),
    ("bench_openreview_judge.py", "--judge-model"),
    ("bench_search_relevance.py", "--judges"),
    ("eval_small_models.py", "--models"),
])
def test_missing_deployment_selection_fails_before_work(script, missing):
    result = _cli(script)
    assert result.returncode == 2
    assert missing in result.stderr
    assert "required" in result.stderr


@pytest.mark.parametrize("script", ["bench_llm_ner.py", "bench_llm_params.py"])
def test_explicit_model_still_requires_endpoint(script):
    result = _cli(script, "--model", "caller-model")
    assert result.returncode == 2
    assert "--base-url" in result.stderr


def test_faithfulness_requires_explicit_judge():
    result = _cli("eval_chunking.py", "--faithfulness")
    assert result.returncode == 2
    assert "--faithfulness requires --judge-model" in result.stderr


@pytest.mark.parametrize("script", [
    "bench_llm_ner.py", "bench_llm_params.py", "bench_openreview_judge.py",
    "bench_search_relevance.py", "eval_small_models.py", "eval_chunking.py",
])
def test_help_does_not_require_backend_or_call_model(script):
    result = _cli(script, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def _sweep(tmp_path, **overrides):
    # Isolate the shell entry point from any caller-owned .env or real commands.
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "sweep_deep_review.sh"
    script.write_text((ROOT / "tools" / script.name).read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
    uv.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", **overrides}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True,
                          text=True, check=False)


def test_sweep_requires_caller_reference(tmp_path):
    result = _sweep(tmp_path)
    assert result.returncode != 0
    assert "Set PAPERS" in result.stderr
    assert "bench_deep_review.py" not in result.stdout


def test_sweep_default_runs_only_explicit_remote_budget(tmp_path):
    result = _sweep(tmp_path, PAPERS="paper-key", REF_PROVIDER="caller-provider",
                    REF_MODEL="caller-model")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("uv run") == 0
    assert result.stdout.count("python tools/bench_deep_review.py") == 2
    assert "--reference-provider caller-provider" in result.stdout
    assert "--reference-model caller-model" in result.stdout
    assert "PHASE 2" not in result.stdout


@pytest.mark.parametrize("phase", ["2", "both", "invalid"])
def test_sweep_local_or_invalid_phase_fails_before_launch(tmp_path, phase):
    result = _sweep(tmp_path, PAPERS="paper-key", REF_PROVIDER="caller-provider",
                    REF_MODEL="caller-model", PHASES=phase)
    assert result.returncode != 0
    assert "bench_deep_review.py" not in result.stdout
