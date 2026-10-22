"""Real transport and frozen artifact counterexamples over public controlled inputs."""
from pathlib import Path

import httpx
import pytest

from tools.method_validation.__main__ import main
from tools.method_validation import priority_run
from tools.method_validation.priority import Config, LABELS, Paper, identity
from tools.method_validation.support import encoded, loads
from test_priority_fixtures import MODEL, fixture, write_ref


def wire(choice='dont_read'):
    return {'model': MODEL, 'answers': {'decision': {'type': 'choice', 'choice': choice,
            'probabilities': dict(zip(LABELS, (0.7, 0.1, 0.1, 0.1))), 'confidence': 0.1}},
            'usage': {'input_tokens': 123, 'output_tokens': 0}}


def test_native_http_firewall_ack_and_actual_choice_not_argmax(tmp_path):
    cfg, papers, path = fixture(tmp_path, native=True)
    requests = []
    def server(request):
        body = loads(request.content)
        assert list(body['questions']['decision']['criteria']) == sorted(LABELS)
        assert 'GRADER_ONLY_SENTINEL_NOT_REAL_HUMAN_LABELS' not in body['state']
        assert set(body) == {'state', 'questions'}
        assert not {'authorization', 'cookie', 'proxy-authorization'} & set(request.headers)
        requests.append(request.content)
        return httpx.Response(200, content=encoded(wire()))
    assert main(['priority', '--config', str(path), '--run'], transport=httpx.MockTransport(server)) == 0
    owner = tmp_path / 'selected/data/priority_comparison/priority-fixture'
    rows = [loads(p.read_bytes()) for p in owner.glob('case-*.json')]
    native = [r for r in rows if r['arm_id'] == 'candidate']
    assert len(requests) == len(native) == 12
    assert all(r['label'] == 'dont_read' and r['readout']['probabilities'][0] == 0.7 for r in native)
    assert all(r['telemetry']['client_retries'] == 0 and r['telemetry']['dispatched'] for r in native)
    assert all((owner / r['terminal']).read_bytes() == encoded(wire()) for r in native)
    assert priority_run.reopen(owner)['winner'] is None


@pytest.mark.parametrize('bad', [b'{', b'{"model":NaN}', b'{"model":"x","model":"x"}',
    encoded(wire()).replace(encoded(MODEL), b'"\\ud800"'),
    encoded(wire()).replace(b'0.7', b'1e9999'),
    encoded(wire()).replace(b'123', b'true'),
    encoded(wire()).replace(b'"dont_read"', b'"foreign"', 1)])
def test_invalid_native_neighbor_stays_unscored_and_raw_is_retained(tmp_path, bad):
    cfg, _, _ = fixture(tmp_path, native=True)
    replies = iter([bad] + [encoded(wire())] * 11)
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=next(replies))))
    rows = [loads((owner / f'case-{i * 3}.json').read_bytes()) for i in range(12)]
    assert rows[0]['status'] == 'invalid_response' and rows[0]['label'] is None
    assert rows[1]['status'] == 'decision' and rows[1]['label'] == 'dont_read'
    assert (owner / rows[0]['terminal']).read_bytes() == bad
    assert priority_run.reopen(owner) == result


def test_transport_timeout_and_model_mismatch_do_not_become_dont_read(tmp_path):
    cfg, _, _ = fixture(tmp_path, native=True)
    calls = 0
    def server(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout('controlled timeout', request=request)
        value = wire()
        if calls == 2:
            value['model'] = 'another-model'
        return httpx.Response(200, content=encoded(value))
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg), transport=httpx.MockTransport(server))
    a, b, c = [loads((owner / f'case-{i * 3}.json').read_bytes()) for i in range(3)]
    assert [a['status'], b['status'], c['status']] == ['timeout', 'model_mismatch', 'decision']
    assert a['label'] is b['label'] is None
    assert b['readout']['observed_model'] == 'another-model'
    assert calls == 12 and priority_run.reopen(owner) == result


def test_publisher_after_effect_failure_is_not_acknowledgement(tmp_path, monkeypatch):
    cfg, _, _ = fixture(tmp_path)
    original = priority_run.publish
    def fail(path, raw):
        original(path, raw)
        if path.name == 'manifest.json':
            raise OSError('publication returned no ACK')
    monkeypatch.setattr(priority_run, 'publish', fail)
    with pytest.raises(OSError, match='returned no ACK'):
        priority_run.run(Config.model_validate(cfg), encoded(cfg))
    owner = tmp_path / 'selected/data/priority_comparison/priority-fixture'
    assert (owner / 'manifest.json').is_file() and (owner / 'FAILED').is_file()
    assert not (owner / 'ACK').exists()
    with pytest.raises(ValueError, match='failed owner'):
        priority_run.reopen(owner)


def test_gold_sidecar_order_is_not_matching_position(tmp_path):
    cfg, _, _ = fixture(tmp_path)
    truth = loads(Path(cfg['gold']['path']).read_bytes())
    truth['labels'].reverse()
    cfg['gold'] = write_ref(tmp_path / 'gold.json', truth)
    owner, _ = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    rows = [loads((owner / f'case-{i * 3}.json').read_bytes()) for i in range(12)]
    assert all(r['gold'] == LABELS[i % 4] for i, r in enumerate(rows))
    assert priority_run.reopen(owner)['arms']['candidate']['ordinal_error']['mean'] == 0


def test_baseline_import_preserves_regression_without_fake_probabilities(tmp_path):
    cfg, papers, _ = fixture(tmp_path)
    for index, baseline in enumerate(cfg['baselines']):
        rows = [{'case_sha256': identity(Paper.model_validate(p)), 'label': LABELS[i % 4],
                 'score': i / 20 if baseline['kind'] == 'ml' else None,
                 'observed_model': baseline['model'], 'elapsed_seconds': 0.01} for i, p in enumerate(papers)]
        artifact = {'kind': baseline['kind'], 'model': baseline['model'],
                    'owner_sha256': priority_run.baseline_owners()[baseline['kind']],
                    'model_artifact_sha256': 'c' * 64, 'trained_at': '2025-01-01T00:00:00Z',
                    'training_families': ['older-training-family'], 'rows': rows}
        baseline['artifact'] = write_ref(tmp_path / f'baseline-{index}.json', artifact)
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    rows = [loads(p.read_bytes()) for p in owner.glob('case-*.json')]
    assert sum(r['status'] == 'decision' for r in rows) == 36
    assert all(r['readout'] is None and r['baseline_native'] is not None
               for r in rows if r['arm_id'] in ('ml', 'llm'))
    assert result['paired']['candidate']['matched_cases'] == 12
    assert result['arms']['ml']['brier'] is None
    assert priority_run.reopen(owner) == result


@pytest.mark.parametrize('change', ['family', 'canonical', 'canonical_same_family', 'future', 'gold', 'bytes', 'work'])
def test_whole_workload_firewalls_refuse_before_artifacts(tmp_path, change):
    cfg, papers, _ = fixture(tmp_path)
    bundle = loads(Path(cfg['inputs']['path']).read_bytes())
    if change == 'family':
        bundle['papers'][0].update(split='development', family=bundle['papers'][1]['family'])
    elif change in ('canonical', 'canonical_same_family'):
        bundle['papers'][0]['canonical_ids'] = bundle['papers'][1]['canonical_ids']
        if change == 'canonical_same_family':
            bundle['papers'][0]['family'] = bundle['papers'][1]['family']
    elif change == 'future':
        bundle['papers'][0].update(split='development', observed_at='2026-12-01T00:00:00Z')
    elif change == 'gold':
        truth = loads(Path(cfg['gold']['path']).read_bytes())
        truth['labels'][0]['identity'] = 'd' * 64
        cfg['gold'] = write_ref(tmp_path / 'gold.json', truth)
    elif change == 'bytes':
        cfg['max_snapshot_bytes'] = 1
    else:
        cfg['max_work_units'] = 1
    cfg['inputs'] = write_ref(tmp_path / 'inputs.json', bundle)
    with pytest.raises(ValueError):
        priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert not (tmp_path / 'selected').exists()
