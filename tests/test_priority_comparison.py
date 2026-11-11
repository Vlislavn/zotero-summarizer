"""Complete CLI outcome over controlled data; no live models or scientific score."""
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from tools.method_validation.__main__ import main
from test_priority_fixtures import fixture


def forbidden(*args, **kwargs):
    raise AssertionError('no implicit HTTP/model/bootstrap/write')


def test_priority_default_admission_is_effect_free(tmp_path, monkeypatch, capsys):
    _, _, path = fixture(tmp_path)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    assert main(['priority', '--config', str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['mode'] == 'inspect-inputs'
    assert output['papers'] == 12
    assert output['model_calls'] == output['writes'] == 0
    assert not (tmp_path / 'selected').exists()


def test_priority_recorded_run_has_real_report_and_verified_reopen(tmp_path, monkeypatch, capsys):
    from tools.method_validation import priority_run

    _, _, path = fixture(tmp_path)
    monkeypatch.setattr(httpx, 'Client', forbidden)
    assert main(['priority', '--config', str(path), '--run']) == 0
    owner = tmp_path / 'selected/data/priority_comparison/priority-fixture'
    assert (owner / 'ACK').is_file() and not (owner / 'FAILED').exists()
    rows = [json.loads(p.read_bytes()) for p in sorted(owner.glob('case-*.json'))]
    assert len(rows) == 36
    assert sum(r['status'] == 'decision' for r in rows) == 12
    assert all(r['label'] is None for r in rows if r['status'] != 'decision')
    assert all(len(r['readout']['probabilities']) == 4 for r in rows if r['readout'])
    report = priority_run.reopen(owner)
    assert report['action'] == 'retain_baseline' and report['winner'] is None
    assert report['arms']['candidate']['ordinal_error']['n'] == 12
    assert report['arms']['ml']['coverage'] == 0
    assert report['arms']['ml']['brier'] is None
    assert report['arms']['candidate']['per_class']['must_read']['support'] == 3
    before = {p.name: p.read_bytes() for p in owner.iterdir()}
    assert main(['priority', '--inspect', str(owner)]) == 0
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
    with pytest.raises(FileExistsError):
        main(['priority', '--config', str(path), '--run'])
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
    capsys.readouterr()


def test_real_priority_module_entry_recorded_run_and_reopen(tmp_path):
    _, _, config = fixture(tmp_path)
    root = Path(__file__).resolve().parents[1]
    def invoke(arguments):
        code = (f'import runpy,sys;sys.path.insert(0,{str(root)!r});'
                f'sys.argv=["tools.method_validation",*{arguments!r}];'
                'runpy.run_module("tools.method_validation",run_name="__main__")')
        return subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=root,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
    result = invoke(['priority', '--config', str(config)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['model_calls'] == 0 and not (tmp_path / 'selected').exists()
    result = invoke(['priority', '--config', str(config), '--run'])
    assert result.returncode == 0, result.stdout + result.stderr
    owner = tmp_path / 'selected/data/priority_comparison/priority-fixture'
    before = {p.name: p.read_bytes() for p in owner.iterdir()}
    result = invoke(['priority', '--inspect', str(owner)])
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(['priority', '--config', str(config), '--run'])
    assert result.returncode != 0
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
