"""Recomputed-hash tampering and writer acknowledgement are separate boundaries."""
import json

import pytest

from test_collection_fixtures import bundle, encoded, save, sha
from tools.method_validation.collections import Config
from tools.method_validation.collections_report import readable, report
from tools.method_validation.collections_run import prepare, reopen, run


def start(tmp_path):
    config, _, _, path = bundle(tmp_path)
    cfg = Config.model_validate(config)
    owner, result = run(cfg, path.read_bytes())
    return cfg, owner, result


def reseal(owner, cfg):
    manifest = json.loads((owner / 'manifest.json').read_bytes())
    rows = [json.loads((owner / f'case-{i}.json').read_bytes()) for i in range(manifest['rows'])]
    prepared = prepare(cfg)
    timings = json.loads((owner / 'paper-times.json').read_bytes())
    result = report(rows, cfg, prepared[0], prepared[1], timings=timings)
    (owner / 'report.json').write_bytes(encoded(result))
    (owner / 'report.md').write_bytes(readable(result).encode())
    manifest['files'] = {p.name: sha(p.read_bytes()) for p in owner.iterdir()
                         if p.name not in ('ACK', 'manifest.json')}
    (owner / 'manifest.json').write_bytes(encoded(manifest))
    (owner / 'ACK').write_text(sha((owner / 'manifest.json').read_bytes()))


@pytest.mark.parametrize('mutation', ['terminal_removed', 'wrong_terminal', 'terminal_swapped',
    'forged_blocked', 'gold', 'request', 'capability', 'orphan', 'recorded_transport', 'missing_ack'])
def test_self_consistent_derived_hashes_cannot_bypass_frozen_branches(tmp_path, mutation):
    cfg, owner, _ = start(tmp_path)
    row = json.loads((owner / 'case-0.json').read_bytes())
    if mutation in ('terminal_removed', 'forged_blocked'):
        (owner / row.pop('terminal')).unlink()
        row.update(status='blocked' if mutation == 'forged_blocked' else 'unavailable', label=None, readout=None,
                   reason=None, telemetry={'reserved': False, 'dispatched': False, 'elapsed_seconds': None,
                                          'remaining_seconds': 1})
    if mutation == 'wrong_terminal': row['terminal'] = 'terminal-1.bin'
    if mutation == 'terminal_swapped':
        (owner / row['terminal']).write_bytes((owner / 'terminal-3.bin').read_bytes())
        row['label'] = 'does_not_belong'
        row['readout']['label'] = 'does_not_belong'
        row['readout']['probabilities'] = [0, 1, 0]
    if mutation == 'gold': row['gold_memberships'] = []
    if mutation == 'request': row['request_sha256'] = '0' * 64
    if mutation == 'capability':
        row.update(capability='top3_suggestions', selected_keys=['A', 'B'], label=None,
                   readout=None, baseline_native={'synthetic_capability': True})
    if mutation == 'recorded_transport': row['telemetry']['terminal_status'] = 'blocked'
    if mutation == 'orphan': (owner / 'terminal-999.bin').write_bytes(b'{}')
    (owner / 'case-0.json').write_bytes(encoded(row))
    reseal(owner, cfg)
    if mutation == 'missing_ack': (owner / 'ACK').unlink()
    with pytest.raises((ValueError, FileNotFoundError)): reopen(owner)


@pytest.mark.parametrize('field,value', [('attempt', True), ('client_retries', False)])
def test_native_transport_counters_are_integers_not_boolean_aliases(tmp_path, field, value):
    import httpx
    from test_collection_native import native
    from test_collection_fixtures import response

    cfg, path = native(tmp_path)
    requests = []

    def server(request):
        requests.append(request.content)
        return httpx.Response(200, content=response())

    owner, original = run(cfg, path.read_bytes(), transport=httpx.MockTransport(server))
    assert reopen(owner) == original and len(requests) == 6
    row = json.loads((owner / 'case-0.json').read_bytes())
    terminal = (owner / row['terminal']).read_bytes()
    row['telemetry'][field] = value
    (owner / 'case-0.json').write_bytes(encoded(row))
    reseal(owner, cfg)
    assert (owner / row['terminal']).read_bytes() == terminal
    with pytest.raises(ValueError):
        reopen(owner)
    assert len(requests) == 6


@pytest.mark.parametrize('missing', ['recording', 'blocked', 'wall'])
def test_valid_terminal_free_outcomes_reopen(tmp_path, missing, monkeypatch):
    import tools.method_validation.collections_run as runtime
    config, inputs, truth, path = bundle(tmp_path)
    inputs['recordings'] = []
    if missing == 'blocked':
        config['arms'][0].update(kind='blocked', blocked_reason='No qualified model')
    if missing == 'wall':
        counter = iter([0, *([10000] * 6)])
        monkeypatch.setattr(runtime, 'monotonic', lambda: next(counter))
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    assert not list(owner.glob('terminal-*.bin'))
    assert reopen(owner) == result
    assert result['arms']['recorded']['complete_papers'] == 0
    if missing == 'wall': assert result['arms']['recorded']['question_statuses'] == {'wall_budget': 6}


@pytest.mark.parametrize('after', [False, True])
@pytest.mark.parametrize('target', ['terminal-0.bin', 'report.json', 'ACK'])
def test_writer_exception_is_not_replaced_by_positive_readback(tmp_path, monkeypatch, after, target):
    import tools.method_validation.collections_run as runtime
    config, _, _, path = bundle(tmp_path)
    actual = runtime.publish
    attempted = []
    def fail(destination, raw):
        attempted.append(destination)
        if destination.name == target:
            if after: actual(destination, raw)
            raise OSError('inert fixture writer failure')
        actual(destination, raw)
    monkeypatch.setattr(runtime, 'publish', fail)
    with pytest.raises(OSError, match='fixture writer failure'):
        run(Config.model_validate(config), path.read_bytes())
    owner = attempted[0].parent
    assert (owner / 'FAILED').is_file()
    assert (owner / target).exists() == after
    with pytest.raises(ValueError, match='failed owner'): reopen(owner)


def test_existing_attempt_cannot_be_reused_and_inspection_needs_no_original_files(tmp_path):
    cfg, owner, result = start(tmp_path)
    before = {p.name: p.read_bytes() for p in owner.iterdir()}
    with pytest.raises(FileExistsError): run(cfg, b'{}')
    cfg.inputs.path.unlink()
    cfg.gold.path.unlink()
    for p in tmp_path.glob('*.bin'): p.unlink()
    assert reopen(owner) == result
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
    with pytest.raises(FileNotFoundError): run(cfg, b'{}')


def test_insufficient_evidence_is_not_a_complete_no_match(tmp_path):
    from test_collection_fixtures import bind, response
    config, inputs, truth, path = bundle(tmp_path)
    inputs['recordings'][0]['terminal'] = bind(tmp_path, 'insufficient.bin', response('insufficient_evidence'))
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    assert result['arms']['recorded']['complete_papers'] == 2
    assert result['arms']['recorded']['question_coverage'] == pytest.approx(5/6)
    assert result['arms']['recorded']['confirmed_native_no_match'] == 1
    assert reopen(owner) == result
