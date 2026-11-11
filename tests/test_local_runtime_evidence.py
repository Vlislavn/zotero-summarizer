"""Native response fixtures reject missing/foreign execution evidence, not guess RAM."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from zotero_summarizer.integrations import ollama_runtime
from zotero_summarizer.integrations.ollama_runtime import OwnedOllamaRuntime
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.setup import load_admission
from zotero_summarizer.services.setup.load_attempt import SetupAttempt


@pytest.fixture
def observed_runtime(monkeypatch, tmp_path):
    runtime = OwnedOllamaRuntime.__new__(OwnedOllamaRuntime)
    runtime.model = 'fixture:latest'
    runtime.provider = ProviderConfig(name='fixture', base_url='http://127.0.0.1:12345/v1', api_key_env='UNUSED')
    runtime._identity = {'provider': runtime.provider.model_dump(mode='json'), 'model': runtime.model}
    runtime.installed = {'digest': 'a' * 64, 'model_path': '/owned/models/blobs/sha256-fixture'}
    runtime.root = {'pid': 123, 'created': 456}
    runtime.batch_size = 128
    runtime.kv_cache_type = 'q8_0'
    runtime.directory = tmp_path
    runtime._guard = lambda: None
    runtime._listener_owned = lambda: True
    row = {'model': runtime.model, 'digest': 'a' * 64, 'context_length': 32768,
           'size': 3000000000, 'size_vram': 2800000000}
    runtime._json = lambda _path: {'models': [row]}
    runner = SimpleNamespace(pid=124, create_time=lambda: 457, cmdline=lambda: ['ollama', 'runner', '-b', '128', '-ub', '128',
                                                                 '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0'])
    root = SimpleNamespace(children=lambda **_kw: [runner])
    monkeypatch.setattr(ollama_runtime, '_same_process', lambda _record: root)
    monkeypatch.setattr(ollama_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(ollama_runtime.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(ollama_runtime.psutil, 'virtual_memory', lambda: SimpleNamespace(available=8 * 1024**3))
    (tmp_path / 'ollama.log').write_text('inference compute library=Metal name="Apple Silicon"\nK (q8_0): 272MiB; V (q8_0): 272MiB\nprompt cache is disabled\ncontext checkpoints disabled\n')
    return runtime, row


@pytest.mark.parametrize('cache,flash', [('f16', '0'), ('q8_0', '1'), ('q5_0', '1'), ('q4_0', '1')])
def test_quantized_native_cache_requires_flash_attention_in_owned_environment(tmp_path, monkeypatch, cache, flash):
    import os

    captured = {}
    runtime = OwnedOllamaRuntime('http://127.0.0.1:11434', 'fixture:latest', tmp_path / 'owned',
                                 tmp_path / 'guard.json', kv_cache_type=cache)
    monkeypatch.setattr(runtime, '_guard', lambda: None)
    monkeypatch.setattr(runtime, '_wait_ready', lambda: None)
    monkeypatch.setattr(ollama_runtime, '_clone_installed', lambda *_args, **_kwargs: {'digest': 'fixture'})
    monkeypatch.setattr(ollama_runtime.shutil, 'which', lambda _: '/native/ollama')
    def start(_command, **options):
        captured.update(options['env'])
        return SimpleNamespace(pid=os.getpid())  # inert constructor fixture, no native process
    monkeypatch.setattr(ollama_runtime.subprocess, 'Popen', start)
    try:
        runtime.__enter__()
        assert captured['OLLAMA_KV_CACHE_TYPE'] == cache
        assert captured['OLLAMA_FLASH_ATTENTION'] == flash
        assert captured['LLAMA_ARG_CACHE_RAM'] == '0'
        assert captured['LLAMA_ARG_CTX_CHECKPOINTS'] == '0'
    finally:
        if runtime._output is not None:
            runtime._output.close()


def test_runtime_reports_actual_native_context_and_bound_unified_memory(observed_runtime):
    runtime, _row = observed_runtime
    result = runtime.observe(runtime.provider, runtime.model)
    assert result['effective_envelope'] == {'context_tokens': 32768, 'placement': 'metal',
                                             'memory_domain': 'apple-unified'}
    assert result['available_bytes'] == 8 * 1024**3
    assert result['native']['runner_pid'] == 124
    assert result['native']['prompt_state_cache_mib'] == 0
    assert result['native']['context_checkpoints'] == 0


@pytest.mark.parametrize('field,value', [('context_length', None), ('context_length', True),
    ('size_vram', None), ('digest', 'foreign'), ('model', 'foreign'), ('size_vram', -1)])
def test_missing_or_foreign_residency_refuses(observed_runtime, field, value):
    runtime, row = observed_runtime
    row[field] = value
    with pytest.raises(ValueError):
        runtime.observe(runtime.provider, runtime.model)


@pytest.mark.parametrize('invalid', [0, -1, True, 1.5, None])
def test_native_output_budget_refuses_invalid_before_preparation(tmp_path, invalid):
    with pytest.raises(ValueError, match='output budget'):
        OwnedOllamaRuntime('http://127.0.0.1:11434', 'installed-model',
                          tmp_path / 'runtime', tmp_path / 'guard.json', max_tokens=invalid)


@pytest.mark.parametrize('maximum', [1, 4096, 8192])
def test_owned_native_output_budget_uses_backend_supported_field(tmp_path, maximum):
    runtime = OwnedOllamaRuntime('http://127.0.0.1:11434', 'installed-model',
                                tmp_path / 'runtime', tmp_path / 'guard.json', max_tokens=maximum)
    assert runtime.provider.max_tokens == maximum
    assert runtime.provider.extra_body == {'max_tokens': maximum}
    assert runtime._identity['provider'] == runtime.provider.model_dump(mode='json')
    assert runtime.process is None


@pytest.mark.parametrize('invalid', [0, -1, True, 1.5, None])
def test_invalid_runtime_rss_budget_refuses_before_preparation(tmp_path, invalid):
    with pytest.raises(ValueError, match='RSS'):
        OwnedOllamaRuntime('http://127.0.0.1:11434', 'fixture:latest', tmp_path / 'runtime',
                          tmp_path / 'guard.json', rss_limit_mib=invalid)


@pytest.mark.parametrize('approved,actual,floor,swap,available,admitted', [
    (None, 4096, 20, 2048, 30, True), (None, 6144, 20, 2048, 30, False),
    (6144, 6144, 20, 2048, 30, True), (6144, 6144, 12, 2048, 30, False),
    (6144, 6144, 20, 3072, 30, False), (6144, 6144, 20, 2048, 19, False),
])
def test_runtime_rss_exception_is_explicit_and_preserves_other_guards(
        tmp_path, monkeypatch, approved, actual, floor, swap, available, admitted):
    import json
    import os

    receipt = tmp_path / 'guard.json'
    record = {'owner': 'local-evaluation-v1', 'state': 'running', 'mode': 'finite', 'guard_stop': None,
              'limits': {'rss_mib': actual, 'min_free_percent': floor, 'swap_growth_mib': swap, 'wall_seconds': 120},
              'guardian': {}, 'sentinel': {}, 'child': {'pid': os.getpid()}}
    receipt.write_text(json.dumps(record))
    monkeypatch.setattr(ollama_runtime, '_same_process', lambda _: object())
    monkeypatch.setattr(os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(ollama_runtime.psutil, 'virtual_memory', lambda: SimpleNamespace(total=100, available=available))
    runtime = OwnedOllamaRuntime('http://127.0.0.1:11434', 'fixture:latest', tmp_path / 'runtime', receipt,
                                **({} if approved is None else {'rss_limit_mib': approved}))
    if admitted:
        assert runtime._guard() == record
    else:
        with pytest.raises(RuntimeError):
            runtime._guard()
    assert runtime.process is None


@pytest.mark.parametrize('effort', [None, 'off', 'low', 'medium', 'high'])
def test_scoped_reasoning_option_is_bound_before_preparation(tmp_path, effort):
    runtime = OwnedOllamaRuntime('http://127.0.0.1:11434', 'installed-model',
                                tmp_path / 'runtime', tmp_path / 'guard.json', thinking_effort=effort)
    assert runtime.provider.thinking_effort == effort
    assert runtime._identity['provider'] == runtime.provider.model_dump(mode='json')
    assert runtime.process is None and runtime.installed is None


def test_unbounded_native_prompt_state_cache_refuses_admission(observed_runtime):
    runtime, _row = observed_runtime
    path = runtime.directory / 'ollama.log'
    path.write_text(path.read_text().replace('prompt cache is disabled',
                                          'prompt cache is enabled, size limit: 8192 MiB'))
    with pytest.raises(ValueError, match='prompt-state caching'):
        runtime.observe(runtime.provider, runtime.model)


def test_unobserved_context_checkpoint_cache_refuses_admission(observed_runtime):
    runtime, _row = observed_runtime
    path = runtime.directory / 'ollama.log'
    path.write_text(path.read_text().replace('context checkpoints disabled',
                                            'context checkpoints enabled, max = 32'))
    with pytest.raises(ValueError, match='context checkpoints'):
        runtime.observe(runtime.provider, runtime.model)


def test_native_llama_server_binds_exact_owned_weight_path(observed_runtime):
    runtime, _row = observed_runtime
    assert runtime._is_model_process(['/native/llama-server', '--model', runtime.installed['model_path']])
    assert not runtime._is_model_process(['/native/llama-server', '--model', '/foreign/weights'])
    assert not runtime._is_model_process(['/native/llama-server', '--model'])
    assert not runtime._is_model_process(['/foreign/server'])


@pytest.mark.parametrize('arguments,expected', [
    (['llama-server', '-b', '128', '-ub', '128'], 128),
    (['llama-server', '-b', '64', '-ub', '64'], 64),
    (['llama-server', '-b', '32', '-ub', '32'], 32),
    (['llama-server', '-b', '1024', '-ub', '128'], None),
    (['llama-server'], None), (['llama-server', '-b'], None),
])
def test_batch_uses_actual_native_arguments_not_requested_options(arguments, expected):
    assert OwnedOllamaRuntime._observed_batch(arguments) == expected


@pytest.mark.parametrize('batch_size', [None, 32, 64, 128])
@pytest.mark.parametrize('effort', [None, 'off', 'low'])
@pytest.mark.parametrize('maximum', [1, 4096])
@pytest.mark.parametrize('rss_limit', [None, 6144])
def test_owned_scope_forwards_explicit_batch_without_global_runtime_registry(tmp_path, monkeypatch, batch_size, effort, maximum, rss_limit):
    from zotero_summarizer.services.setup import _local_runtime
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt
    from zotero_summarizer.settings import Settings

    class Runtime:
        def __init__(self, *_args, **options):
            self.options = options
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def observe(self, _provider, _model):
            return {'batch_size': self.options['batch_size'], 'thinking_effort': self.options['thinking_effort'],
                    'max_tokens': self.options['max_tokens'], 'rss_limit_mib': self.options['rss_limit_mib']}

    monkeypatch.setattr(_local_runtime, 'OwnedOllamaRuntime', Runtime)
    settings = Settings.load(project_root=tmp_path)
    attempt = SetupAttempt()
    with attempt.scope():
        with _local_runtime._owned_local_runtime(settings, 'synthetic', settings.data_dir / 'runtime',
                settings.data_dir / 'guard.json', thinking_effort=effort, max_tokens=maximum,
                **({} if rss_limit is None else {'rss_limit_mib': rss_limit}),
                **({} if batch_size is None else {'batch_size': batch_size})):
            assert attempt.observe_runtime(None, 'synthetic') == {
                'batch_size': batch_size or 128, 'thinking_effort': effort, 'max_tokens': maximum,
                'rss_limit_mib': rss_limit or 4096}
        assert attempt.observe_runtime(None, 'synthetic')['effective_envelope'] is None


@pytest.mark.parametrize('kind', ['f16', 'q8_0', 'q5_0', 'q4_0'])
def test_actual_native_kv_flags_establish_cache_type(observed_runtime, kind):
    runtime, _row = observed_runtime
    (runtime.directory / 'ollama.log').write_text(f'K ({kind}): 32MiB; V ({kind}): 32MiB')
    assert runtime._observed_cache(['--cache-type-k', kind, '--cache-type-v', kind]) == kind
    assert runtime._observed_cache(['--cache-type-k', kind, '--cache-type-v', 'unknown']) is None


def test_default_cache_needs_both_explicit_native_log_facts(observed_runtime):
    runtime, _row = observed_runtime
    (runtime.directory / 'ollama.log').write_text('No native cache facts')
    assert runtime._observed_cache([]) is None
    (runtime.directory / 'ollama.log').write_text('K (f16): 32MiB; V (f16): 32MiB')
    assert runtime._observed_cache([]) == 'f16'
    (runtime.directory / 'ollama.log').write_text('K (f16): 32MiB')
    assert runtime._observed_cache([]) is None


@pytest.mark.parametrize('requested', ['q4_0', 'q5_0'])
def test_requested_quantized_flags_cannot_hide_actual_native_q8_fallback(observed_runtime, requested):
    runtime, _row = observed_runtime
    assert runtime._observed_cache(['--cache-type-k', requested, '--cache-type-v', requested]) is None


def test_declared_q4_cannot_accept_actual_q8(observed_runtime):
    runtime, _row = observed_runtime
    runtime.kv_cache_type = 'q4_0'
    with pytest.raises(ValueError, match='KV cache type'):
        runtime.observe(runtime.provider, runtime.model)


def test_requested64_refuses_actual128_even_when_context_and_digest_match(observed_runtime):
    runtime, _row = observed_runtime
    runtime.batch_size = 64
    with pytest.raises(ValueError, match='execution batch'):
        runtime.observe(runtime.provider, runtime.model)


def test_requested_context_is_not_substituted_for_actual_context(observed_runtime):
    runtime, row = observed_runtime
    row['context_length'] = 4096
    assert runtime.observe(runtime.provider, runtime.model)['effective_envelope']['context_tokens'] == 4096


def test_provider_identity_and_listener_are_independently_current(observed_runtime):
    runtime, _row = observed_runtime
    changed = runtime.provider.model_copy(update={'num_ctx': 65536})
    with pytest.raises(ValueError):
        runtime.observe(changed, runtime.model)
    runtime._listener_owned = lambda: False
    with pytest.raises(ValueError):
        runtime.observe(runtime.provider, runtime.model)


def test_device_allocation_without_native_memory_domain_is_not_host_availability(observed_runtime):
    runtime, _row = observed_runtime
    (runtime.directory / 'ollama.log').write_text('device inventory unavailable\nK (q8_0): 32MiB; V (q8_0): 32MiB\nprompt cache is disabled\ncontext checkpoints disabled')
    with pytest.raises(ValueError, match='memory domain'):
        runtime.observe(runtime.provider, runtime.model)


def test_only_current_attempt_can_supply_runtime_evidence(observed_runtime):
    runtime, _row = observed_runtime
    attempt = SetupAttempt()
    assert load_admission._observe_executing_runtime(runtime.provider, runtime.model)['effective_envelope'] is None
    with attempt.scope(), attempt.own_runtime(runtime):
        assert load_admission._observe_executing_runtime(runtime.provider, runtime.model)['effective_envelope']
    assert load_admission._observe_executing_runtime(runtime.provider, runtime.model)['effective_envelope'] is None


def test_empty_residency_and_foreign_second_model_remain_nonadmitted(observed_runtime):
    runtime, row = observed_runtime
    runtime._json = lambda _path: {'models': []}
    assert runtime.observe(runtime.provider, runtime.model)['effective_envelope'] is None
    runtime._json = lambda _path: {'models': [row, deepcopy(row)]}
    with pytest.raises(ValueError):
        runtime.observe(runtime.provider, runtime.model)
