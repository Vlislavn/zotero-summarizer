"""Inert native processes exercise deadline, orphan and monitor-death reaping."""
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from types import SimpleNamespace

from zotero_summarizer.integrations import _process_guard


@pytest.mark.parametrize('condition', ['closed', 'live_pid', 'reused_pid', 'orphan_group', 'escaped_runtime', 'live_listener',
                                       'opaque_unrelated', 'opaque_native'])
@pytest.mark.parametrize('stop_kind', ['resource_guard', 'failed_worker_exit'])
@pytest.mark.parametrize('rss_mib', [4096, 6144])
def test_owner_recovery_requires_kernel_absence_not_native_success(tmp_path, monkeypatch, condition, stop_kind, rss_mib):
    import os
    import socket

    runtime = tmp_path / 'runtime'
    record = {'owner': 'local-evaluation-v1', 'mode': 'finite', 'state': 'stopped', 'guard_stop': 'rss_mib',
              'limits': {**_process_guard._LIMITS, 'rss_mib': rss_mib},
              **{key: {'pid': number, 'created': 100.0} for number, key in enumerate(
                  ['guardian', 'child', 'sentinel'], start=900001)}}
    if stop_kind == 'failed_worker_exit':
        record.update(guard_stop=None, returncode=1)
    if rss_mib != 4096:
        with pytest.raises(ValueError, match='stopped finite'):
            _process_guard._termination_evidence(record, runtime, 'http://127.0.0.1:1')
    monkeypatch.setattr(psutil, 'pid_exists', lambda _: condition in {'live_pid', 'reused_pid'})
    arguments = ['llama-server', '--model', str(runtime / 'weights')] if condition == 'escaped_runtime' else []
    if condition in {'opaque_native', 'opaque_unrelated'}:
        arguments = None
    process = SimpleNamespace(pid=900004, info={'uids': SimpleNamespace(real=os.getuid()),
        'name': 'login' if condition == 'opaque_unrelated' else 'llama-server', 'create_time': 200.0,
        'cmdline': arguments})
    monkeypatch.setattr(psutil, 'process_iter', lambda *_, **__: [process])
    monkeypatch.setattr(os, 'getpgid', lambda _: 900002 if condition == 'orphan_group' else 900010)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        if condition != 'live_listener':
            listener.close()
        if condition in {'closed', 'opaque_unrelated'}:
            evidence = _process_guard._termination_evidence(record, runtime, f'http://127.0.0.1:{port}', rss_limit_mib=rss_mib)
            assert evidence['group_absent'] and evidence['listener_absent']
            assert evidence['successful'] is False and evidence['native_terminal_observed'] is False
        else:
            with pytest.raises((ValueError, OSError)):
                _process_guard._termination_evidence(record, runtime, f'http://127.0.0.1:{port}', rss_limit_mib=rss_mib)


@pytest.mark.parametrize('returncode', [0, None, True])
def test_success_or_missing_worker_exit_does_not_authorize_termination_ack(tmp_path, returncode):
    record = {'owner': 'local-evaluation-v1', 'mode': 'finite', 'state': 'stopped', 'guard_stop': None,
              'returncode': returncode, 'limits': dict(_process_guard._LIMITS)}
    with pytest.raises(ValueError, match='stopped finite'):
        _process_guard._termination_evidence(record, tmp_path / 'runtime', 'http://127.0.0.1:1')


@pytest.mark.parametrize('rss_mib', [4096, 6144])
def test_monitor_keeps_original_time_and_swap_baseline(tmp_path, monkeypatch, rss_mib):
    observed_baselines = []
    def sample(_root, initial_swap):
        observed_baselines.append(initial_swap)
        return {'rss_mib': 10, 'swap_growth_mib': 0, 'free_percent': 50, 'processes': []}
    monkeypatch.setattr(_process_guard, '_sample', sample)
    monkeypatch.setattr(_process_guard.time, 'monotonic', lambda: 15)
    process = SimpleNamespace(poll=lambda: None)
    record = {'child': {'pid': 1}, 'limits': {**_process_guard._LIMITS, 'rss_mib': rss_mib, 'wall_seconds': 3}}
    _process_guard._monitor(process, process, record, tmp_path / 'guard.json', [], (10, 77))
    assert record['guard_stop'] == 'wall_seconds' and record['elapsed_seconds'] == 5
    assert observed_baselines == [77]


@pytest.mark.parametrize('rss_mib,used,free,swap,expected', [
    (4096, 4200, 50, 0, 'rss_mib'), (6144, 4200, 50, 0, None),
    (6144, 6200, 50, 0, 'rss_mib'), (6144, 4200, 19, 0, 'free_percent'),
    (6144, 4200, 50, 2049, 'swap_growth_mib'),
])
def test_explicit_rss_budget_never_changes_other_tripwires(rss_mib, used, free, swap, expected):
    limits = {**_process_guard._LIMITS, 'rss_mib': rss_mib}
    assert _process_guard._tripwire({'rss_mib': used, 'free_percent': free, 'swap_growth_mib': swap}, limits) == expected
    assert _process_guard._LIMITS['rss_mib'] == 4096


@pytest.mark.parametrize('invalid', [0, -1, True, None, 1.5])
def test_invalid_rss_limits_refuse(invalid):
    with pytest.raises(ValueError, match='RSS'):
        _process_guard._limits(invalid)


def _wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError('Native process condition did not settle')


def _running_record(path):
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data if data.get('state') == 'running' else None


def _gone(pid):
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _launch(tmp_path, script, wall=30, rss_mib=None):
    receipt = tmp_path / 'guard.json'
    process = subprocess.Popen([sys.executable, _process_guard.__file__, '--cwd', str(tmp_path),
        '--log', str(tmp_path / 'guard.log'), '--receipt', str(receipt), '--wall-seconds', str(wall),
        *([] if rss_mib is None else ['--rss-mib', str(rss_mib)]), '--', sys.executable, '-c', script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return process, receipt


@pytest.mark.parametrize('rss_mib', [None, 6144])
def test_finite_guard_keeps_caps_and_reaps_on_deadline(tmp_path, rss_mib):
    guard, receipt = _launch(tmp_path, 'import time; time.sleep(60)', wall=2, rss_mib=rss_mib)
    try:
        running = _wait_for(lambda: _running_record(receipt))
        assert running['limits']['min_free_percent'] == 20
        assert running['limits']['rss_mib'] == (rss_mib or 4096)
        assert running['limits']['swap_growth_mib'] == 2048
        assert guard.wait(timeout=12) == 125
        final = json.loads(receipt.read_text())
        assert final['guard_stop'] == 'wall_seconds'
        assert sum(row['rss_mib'] for row in final['peak_rss_processes']) == final['peak_rss_mib']
        assert all(row['pid'] > 0 and row['created'] > 0 and row['name'] for row in final['peak_rss_processes'])
        assert final['returncode'] < 0  # actual signal exit, not psutil/Popen ECHILD -> zero
        _wait_for(lambda: _gone(running['child']['pid']))
    finally:
        if guard.poll() is None:
            guard.terminate()
            guard.wait(timeout=12)


def test_guard_reaps_orphan_even_after_main_command_return(tmp_path):
    child_path = tmp_path / 'orphan.json'
    script = ("import subprocess,sys,json; from pathlib import Path; "
              "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
              f"Path({str(child_path)!r}).write_text(json.dumps({{'pid':p.pid}}))")
    guard, _receipt = _launch(tmp_path, script)
    try:
        _wait_for(child_path.exists)
        pid = json.loads(child_path.read_text())['pid']
        assert guard.wait(timeout=15) == 0
        _wait_for(lambda: _gone(pid))
    finally:
        if guard.poll() is None:
            guard.terminate()
            guard.wait(timeout=12)


def test_pipe_sentinel_reaps_when_monitor_is_killed(tmp_path):
    guard, receipt = _launch(tmp_path, 'import time; time.sleep(60)')
    try:
        running = _wait_for(lambda: _running_record(receipt))
        guard.kill()
        guard.wait(timeout=5)
        _wait_for(lambda: _gone(running['child']['pid']))
        _wait_for(lambda: _gone(running['sentinel']['pid']))
    finally:
        if guard.poll() is None:
            guard.terminate()
            guard.wait(timeout=12)
