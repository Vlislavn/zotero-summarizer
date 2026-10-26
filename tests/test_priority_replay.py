"""Development firewall, missing evidence, and semantically recomputed reopening."""
from pathlib import Path

import httpx
import pytest

from tools.method_validation import priority_run
from tools.method_validation.priority import Config, Paper, identity
from tools.method_validation.support import digest, encoded, loads, sha
from test_priority_boundaries import wire
from test_priority_replay_states import rewrite
from test_priority_fixtures import fixture, write_ref


def test_threshold_requires_development_before_holdout_and_remains_abstention(tmp_path):
    cfg, _, _ = fixture(tmp_path, native=True)
    cfg['arms'][0]['abstention_threshold'] = 0.2
    with pytest.raises(ValueError):
        Config.model_validate(cfg)
    bundle = loads(Path(cfg['inputs']['path']).read_bytes())
    bundle['papers'][0].update(split='development', observed_at='2025-12-01T00:00:00Z')
    cfg['inputs'] = write_ref(tmp_path / 'inputs.json', bundle)
    cfg['calibration'] = {'split_sha256': digest([(p['paper_id'], p['family'], p['split'])
                                                for p in bundle['papers']]),
                          'development_families': ['family-0'], 'method': 'fixed synthetic control threshold',
                          'frozen_at': '2026-01-02T00:00:00Z'}
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=encoded(wire()))))
    rows = [loads((owner / f'case-{i * 3}.json').read_bytes()) for i in range(12)]
    assert all(r['status'] == 'threshold_abstention' and r['label'] is None for r in rows)
    assert all(r['readout']['label'] == 'dont_read' for r in rows)
    assert result['arms']['candidate']['coverage'] == 0
    assert priority_run.reopen(owner) == result
    cfg['calibration']['development_families'] = ['family-1']
    with pytest.raises(ValueError, match='firewall'):
        priority_run.prepare(Config.model_validate(cfg))


def test_missing_input_has_no_dispatch_and_does_not_abort_neighbor(tmp_path):
    cfg, _, _ = fixture(tmp_path, native=True)
    bundle = loads(Path(cfg['inputs']['path']).read_bytes())
    bundle['papers'][0]['abstract'] = None
    truth = loads(Path(cfg['gold']['path']).read_bytes())
    truth['labels'][0]['identity'] = identity(Paper.model_validate(bundle['papers'][0]))
    cfg['inputs'] = write_ref(tmp_path / 'inputs.json', bundle)
    cfg['gold'] = write_ref(tmp_path / 'gold.json', truth)
    calls = []
    def server(request):
        calls.append(request)
        return httpx.Response(200, content=encoded(wire()))
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg), transport=httpx.MockTransport(server))
    row = loads((owner / 'case-0.json').read_bytes())
    assert row['status'] == 'missing_input' and row['label'] is None and len(calls) == 11
    assert 'terminal' not in row and row['telemetry']['dispatched'] is False
    assert priority_run.reopen(owner) == result


@pytest.mark.parametrize('target', ['case-0.json', 'report.json'])
def test_reopen_recomputes_projection_even_after_self_consistent_file_hash_edit(tmp_path, target):
    cfg, _, _ = fixture(tmp_path)
    owner, _ = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    value = loads((owner / target).read_bytes())
    if target.startswith('case-'):
        value['label'] = 'dont_read'
        value['readout']['label'] = 'dont_read'
    else:
        value['arms']['candidate']['ordinal_error']['mean'] = 99
    raw = encoded(value)
    (owner / target).write_bytes(raw)
    manifest = loads((owner / 'manifest.json').read_bytes())
    manifest['files'][target] = sha(raw)
    mraw = encoded(manifest)
    (owner / 'manifest.json').write_bytes(mraw)
    (owner / 'ACK').write_bytes(sha(mraw).encode())
    with pytest.raises(ValueError, match='projection mismatch'):
        priority_run.reopen(owner)


@pytest.mark.parametrize('mutation', ['attempt', 'attempt_bool', 'dispatched', 'retained', 'foreign'])
def test_native_dispatch_bytes_and_inventory_match_admitted_work(tmp_path, mutation):
    cfg, papers, _ = fixture(tmp_path, native=True)
    config = Config.model_validate(cfg)
    requests = []

    def server(request):
        requests.append(request.content)
        return httpx.Response(200, content=encoded(wire()))

    owner, report = priority_run.run(config, encoded(cfg), transport=httpx.MockTransport(server))
    assert priority_run.reopen(owner) == report and len(requests) == len(papers)
    row = loads((owner / 'case-0.json').read_bytes())
    assert row['arm_id'] == config.arms[0].arm_id
    terminal_before = (owner / row['terminal']).read_bytes()
    if mutation in ('attempt', 'attempt_bool'):
        row['telemetry']['attempt'] = True if mutation == 'attempt_bool' else 0
    elif mutation == 'dispatched':
        row['telemetry']['dispatched'] = False
    elif mutation == 'retained':
        row['telemetry']['retained_bytes'] = len(terminal_before) + 1
    else:
        foreign = owner / 'foreign.bin'
        foreign.write_bytes(b'unadmitted private fixture file')
        manifest = loads((owner / 'manifest.json').read_bytes())
        manifest['files'][foreign.name] = sha(foreign.read_bytes())
        (owner / 'manifest.json').write_bytes(encoded(manifest))
    rewrite(owner, config, row)
    assert (owner / row['terminal']).read_bytes() == terminal_before
    with pytest.raises(ValueError):
        priority_run.reopen(owner)
    assert len(requests) == len(papers)


def test_unqualified_native_and_work_limits_do_not_dispatch(tmp_path):
    cfg, _, _ = fixture(tmp_path, native=True)
    cfg['arms'][0]['access'] = 'unknown'
    def forbidden(request):
        raise AssertionError('blocked arm cannot dispatch')
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg), transport=httpx.MockTransport(forbidden))
    rows = [loads((owner / f'case-{i * 3}.json').read_bytes()) for i in range(12)]
    assert all(r['status'] == 'blocked' and r['label'] is None for r in rows)
    assert result['arms']['candidate']['coverage'] is None
    assert priority_run.reopen(owner) == result
