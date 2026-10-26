"""Admission-derived priority replay states; synthetic inputs and inert transport."""
from pathlib import Path

import httpx
import pytest

from tools.method_validation import priority_run as owner_module
from tools.method_validation.priority import Config
from tools.method_validation.support import encoded, loads, sha
from test_priority_fixtures import fixture, write_ref


def attempt(tmp_path, *, mode='recorded', monkeypatch=None):
    cfg, _, _ = fixture(tmp_path, native=mode == 'native')
    bundle = loads(Path(cfg['inputs']['path']).read_bytes())
    if mode == 'recorded':
        bundle['recordings'][0]['terminal'] = write_ref(tmp_path / 'invalid.bin', b'not JSON')
    elif mode in ('blocked', 'missing'):
        bundle['recordings'] = []
        if mode == 'blocked':
            cfg['arms'][0]['kind'] = 'blocked'
    cfg['inputs'] = write_ref(tmp_path / 'inputs.json', bundle)
    cfg['min_coverage'] = 1
    config = Config.model_validate(cfg)
    if mode == 'wall':
        ticks = iter([0.0, *(config.max_run_seconds + i for i in range(1, 13))])
        monkeypatch.setattr(owner_module, 'monotonic', lambda: next(ticks))
    def reply(request):
        return httpx.Response(200, content=b'not JSON')
    owner, result = owner_module.run(config, encoded(cfg), transport=httpx.MockTransport(reply))
    return owner, config, result


def rewrite(owner, cfg, row):
    manifest = loads((owner / 'manifest.json').read_bytes())
    (owner / 'case-0.json').write_bytes(encoded(row))
    rows = [loads((owner / f'case-{i}.json').read_bytes()) for i in range(manifest['rows'])]
    prepared = owner_module.prepare(cfg)
    result = owner_module.report(rows, cfg, *prepared[:2])
    (owner / 'report.json').write_bytes(encoded(result))
    (owner / 'report.md').write_text(owner_module.readable(result))
    manifest['files'] = {n: sha((owner / n).read_bytes()) for n in manifest['files'] if (owner / n).exists()}
    raw = encoded(manifest)
    (owner / 'manifest.json').write_bytes(raw)
    (owner / 'ACK').write_bytes(sha(raw).encode())
    return result


@pytest.mark.parametrize('status,remove_bytes', [('blocked', False), ('blocked', True),
    ('wall_budget', False), ('wall_budget', True), ('unavailable', True)])
def test_terminal_omission_does_not_hide_an_admitted_recording(tmp_path, status, remove_bytes):
    owner, cfg, original = attempt(tmp_path)
    assert original['arms']['candidate']['coverage'] == 11 / 12
    assert original['gates']['candidate']['coverage'] is False
    row = loads((owner / 'case-0.json').read_bytes())
    terminal = row.pop('terminal')
    row.update(status=status)
    if status in ('blocked', 'unavailable'):
        row['reason'] = None
        row['telemetry'] = {'reserved': False, 'dispatched': False, 'elapsed_seconds': None,
                            'remaining_seconds': cfg.max_run_seconds}
    else:
        row['telemetry']['remaining_seconds'] = -1.0
    if remove_bytes:
        (owner / terminal).unlink()
    forged = rewrite(owner, cfg, row)
    if status == 'blocked':
        assert forged['arms']['candidate']['coverage'] == 1
        assert forged['gates']['candidate']['coverage'] is True
    with pytest.raises(ValueError):
        owner_module.reopen(owner)


def test_native_terminal_status_cannot_forge_a_blocked_arm(tmp_path):
    owner, cfg, _ = attempt(tmp_path, mode='native')
    row = loads((owner / 'case-0.json').read_bytes())
    row.update(status='blocked')
    row['telemetry']['terminal_status'] = 'blocked'
    rewrite(owner, cfg, row)
    with pytest.raises(ValueError):
        owner_module.reopen(owner)


@pytest.mark.parametrize('mode,expected', [('recorded', 'invalid_response'), ('blocked', 'blocked'),
    ('missing', 'unavailable'), ('wall', 'wall_budget')])
def test_actual_unattempted_or_malformed_priority_rows_reopen(tmp_path, monkeypatch, mode, expected):
    owner, cfg, result = attempt(tmp_path, mode=mode, monkeypatch=monkeypatch)
    assert loads((owner / 'case-0.json').read_bytes())['status'] == expected
    assert owner_module.reopen(owner) == result


def test_missing_recording_requires_its_frozen_request_hash(tmp_path):
    owner, cfg, _ = attempt(tmp_path, mode='missing')
    row = loads((owner / 'case-0.json').read_bytes())
    row['request_sha256'] = '0' * 64
    rewrite(owner, cfg, row)
    with pytest.raises(ValueError):
        owner_module.reopen(owner)
