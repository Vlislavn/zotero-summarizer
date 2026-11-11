import argparse

import pytest

from tests._interpretation_cohort import cohort


def parser():
    from zotero_summarizer.cli._faithbench import register_faithbench
    root = argparse.ArgumentParser()
    register_faithbench(root.add_subparsers())
    return root


def test_default_inspection_never_constructs_models_or_writes_output(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.cli import _interpretation
    from zotero_summarizer.services.llm import factory

    settings, path, _, _, _ = cohort(tmp_path)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setattr(_interpretation.Settings, 'load', lambda **kwargs: settings)
    monkeypatch.setattr(factory, 'build_client_for_stage', lambda *args: pytest.fail('inspection constructed a provider'))
    args = parser().parse_args(['faithbench', 'interpretation', '--recipe', str(path)])
    assert args.func(args) == 0
    import json
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'INSPECTED_NOT_EXECUTED'
    assert output['assigned_per_condition'] == 80
    assert output['model_constructed'] is False
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('flags', [
    ['--run', '--recipe', 'absent.json'],
    ['--labels', 'absent.json'],
    ['--run-id', 'a' * 32, '--run', '--capture-local'],
])
def test_inconsistent_or_unconsented_execution_refuses_before_settings(flags, monkeypatch):
    from zotero_summarizer.cli import _interpretation
    monkeypatch.setattr(_interpretation.Settings, 'load', lambda **kwargs: pytest.fail('preflight loaded Settings'))
    args = parser().parse_args(['faithbench', 'interpretation', *flags])
    with pytest.raises(ValueError):
        args.func(args)


@pytest.mark.parametrize('flags', [
    ['--run', '--recipe', 'absent.json'],
    ['--labels', 'absent.json'],
    ['--run-id', 'a' * 32, '--run', '--capture-local'],
])
def test_actual_cli_dispatch_validates_before_settings(flags, monkeypatch):
    from zotero_summarizer.cli import main
    from zotero_summarizer.settings import Settings
    monkeypatch.setattr(Settings, 'load', lambda **kwargs: pytest.fail('dispatch loaded Settings before validation'))
    with pytest.raises(SystemExit) as stopped:
        main(['faithbench', 'interpretation', *flags])
    assert stopped.value.code == 2


@pytest.mark.parametrize('strategy', ['prefix', 'rank', 'map_reduce'])
def test_actual_cli_uses_current_runtime_role_and_thinking_owners(strategy, tmp_path, monkeypatch):
    import inspect
    from pathlib import Path
    from zotero_summarizer.cli import _interpretation
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.faithbench import _interpretation as runner
    from zotero_summarizer.services.llm import factory

    settings, path, _, config, _ = cohort(tmp_path)
    settings.config_path = tmp_path / 'unused.yaml'
    config.quality_review.chunk_strategy = strategy
    config.llm_enabled = True
    monkeypatch.setattr(_interpretation.Settings, 'load', lambda **kwargs: settings)
    monkeypatch.setattr(_common, 'read_config', lambda path: config)
    events = []

    def default(profile):
        events.append(('default', profile.stage))
        return object()

    def overridden(provider, model, *, enable_thinking):
        events.append(('override', enable_thinking))
        return object()

    def execute(settings, snapshot, received_config, profiles, build):
        assert received_config is config
        clients = {role: build(role, profile) if len(inspect.signature(build).parameters) > 1 else build(profile)
                   for role, profile in profiles.items()}
        assert profiles['map'].stage == ('feed' if strategy == 'map_reduce' else 'deep_review')
        assert (clients['map'] is clients['qa']) == (strategy != 'map_reduce')
        assert clients['reduce'] is not clients['qa']
        assert events.count(('override', profiles['reduce'].provider.thinking_on)) == 1
        return Path('a' * 32)

    monkeypatch.setattr(factory, 'build_client_for_stage', default)
    monkeypatch.setattr(factory, 'build_client_for_provider', overridden)
    monkeypatch.setattr(runner, 'execute', execute)
    args = parser().parse_args(['faithbench', 'interpretation', '--recipe', str(path), '--run', '--capture-local'])
    assert args.func(args) == 0


def test_existing_judge_arguments_survive_registration_subtraction():
    from zotero_summarizer.services.faithbench._constants import DEFAULT_JUDGE_MODEL
    args = parser().parse_args(['faithbench', 'judge', '--run-id', 'existing', '--force'])
    assert args.judge_model == DEFAULT_JUDGE_MODEL
    assert args.run_id == 'existing'
    assert args.force is True
