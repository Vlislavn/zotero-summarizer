"""Replay state consistency under unsigned, self-consistent output substitutions."""
import httpx
import pytest

from tools.method_validation import support_run as owner_module
from tools.method_validation.support import digest, encoded, loads, sha
from test_support_fixtures import fixture, native_wire, update_input, validated


def attempt(tmp_path, *, mode='recorded', clock=None, monkeypatch=None):
    cfg, bundle, case, _ = fixture(tmp_path, kind='native' if mode == 'native' else 'recorded', raw=b'not JSON')
    second = dict(case, pair_id='valid-neighbor', criterion='A separate fixture criterion?')
    bundle['cases'].append(second)
    if mode == 'recorded':
        raw = encoded(native_wire())
        path = tmp_path / 'neighbor.bin'
        path.write_bytes(raw)
        bundle['recordings'].append({'arm_id': 'native', 'case_sha256': digest(second),
            'terminal': {'path': str(path), 'sha256': sha(raw)}})
    elif mode in ('blocked', 'missing'):
        bundle['recordings'] = []
        if mode == 'blocked':
            cfg['arms'][0]['kind'] = 'blocked'
    update_input(cfg, bundle)
    cfg['min_coverage'] = 1
    config = validated(cfg)
    if clock is not None:
        ticks = iter([0.0, config.max_run_seconds + 1, config.max_run_seconds + 2])
        monkeypatch.setattr(owner_module, 'monotonic', lambda: next(ticks))
    def reply(request):
        return httpx.Response(200, content=b'not JSON')
    owner, report = owner_module.run(config, encoded(cfg), transport=httpx.MockTransport(reply))
    return owner, config, owner_module.prepare(config)[0], report


def rewrite(owner, cfg, bundle, row):
    manifest = loads((owner / 'manifest.json').read_bytes())
    (owner / 'pair-0.json').write_bytes(encoded(row))
    rows = [loads((owner / f'pair-{i}.json').read_bytes()) for i in range(manifest['rows'])]
    result = owner_module.report(rows, cfg, bundle)
    (owner / 'report.json').write_bytes(encoded(result))
    (owner / 'report.md').write_text(owner_module.readable(result))
    manifest['files'] = {n: sha((owner / n).read_bytes()) for n in manifest['files'] if (owner / n).exists()}
    raw = encoded(manifest)
    (owner / 'manifest.json').write_bytes(raw)
    (owner / 'ACK').write_bytes(sha(raw).encode())
    return result


@pytest.mark.parametrize('status,remove_bytes', [('blocked', False), ('blocked', True),
    ('wall_budget', False), ('wall_budget', True), ('unavailable', True)])
def test_terminal_omission_cannot_authorize_an_unattempted_recording(tmp_path, status, remove_bytes):
    owner, cfg, bundle, original = attempt(tmp_path)
    assert original['arms']['native']['coverage'] == 0.5
    assert original['gates']['native']['coverage'] is False
    row = loads((owner / 'pair-0.json').read_bytes())
    assert row['status'] == 'invalid_response'
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
    forged = rewrite(owner, cfg, bundle, row)
    if status == 'blocked':
        assert forged['arms']['native']['coverage'] == 1
        assert forged['gates']['native']['coverage'] is True
    with pytest.raises(ValueError):
        owner_module.reopen(owner)


def test_live_terminal_status_cannot_be_a_predispatch_blockage(tmp_path):
    owner, cfg, bundle, _ = attempt(tmp_path, mode='native')
    row = loads((owner / 'pair-0.json').read_bytes())
    row.update(status='blocked')
    row['telemetry']['terminal_status'] = 'blocked'
    rewrite(owner, cfg, bundle, row)
    with pytest.raises(ValueError):
        owner_module.reopen(owner)


@pytest.mark.parametrize('mode,expected', [('recorded', 'invalid_response'), ('blocked', 'blocked'),
    ('missing', 'unavailable'), ('wall', 'wall_budget')])
def test_real_producer_states_reopen_without_changing_denominators(tmp_path, monkeypatch, mode, expected):
    owner, cfg, bundle, original = attempt(tmp_path, mode='recorded' if mode == 'wall' else mode,
                                          clock=True if mode == 'wall' else None, monkeypatch=monkeypatch)
    assert loads((owner / 'pair-0.json').read_bytes())['status'] == expected
    assert owner_module.reopen(owner) == original


def test_missing_recording_still_requires_its_actual_request_identity(tmp_path):
    owner, cfg, bundle, _ = attempt(tmp_path, mode='missing')
    row = loads((owner / 'pair-0.json').read_bytes())
    row['request_sha256'] = '0' * 64
    rewrite(owner, cfg, bundle, row)
    with pytest.raises(ValueError):
        owner_module.reopen(owner)
