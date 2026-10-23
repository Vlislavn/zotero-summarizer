"""Native mock wire, exact raw ACK ordering, configuration and resource observation controls."""
import json

import httpx
import pytest

from test_collection_fixtures import LABELS, bundle, response, save
from tools.method_validation.collections import Config, Result
from tools.method_validation.collections_run import prepare, reopen, run


def native(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    config['arms'][0].update(kind='native', endpoint='https://example.invalid/v1/systemone',
        eligibility='qualified', access='qualified', runtime_sha256='0'*64, checkpoint_sha256='0'*64,
        quantization='fixture', model_source='fixture', licence='fixture', context_verified=True,
        forward_passes_bound=1)
    config.update(public_input_consent=True, external_guard='parent inert HTTPX only')
    inputs['recordings'] = []
    return Config.model_validate(save(tmp_path, config, inputs, truth)), path


@pytest.mark.parametrize('outcome,status', [('valid', 'decision'), ('malformed', 'invalid_response'),
    ('timeout', 'timeout'), ('unavailable', 'unavailable'), ('oversized', 'response_too_large')])
def test_real_adapter_preserves_every_terminal_including_operational_failures(tmp_path, outcome, status):
    cfg, path = native(tmp_path)
    calls = []
    def reply(request):
        body = json.loads(request.content)
        assert list(body['questions']['decision']['criteria']) == list(LABELS)
        assert 'gold_memberships' not in request.content.decode()
        calls.append(request.content)
        if outcome == 'timeout': raise httpx.ReadTimeout('fixture', request=request)
        if outcome == 'unavailable': return httpx.Response(503, content=b'fixture unavailable')
        if outcome == 'oversized': return httpx.Response(200, content=b'x'*(cfg.max_response_bytes + 1))
        return httpx.Response(200, content=response() if outcome == 'valid' else b'{')
    owner, report = run(cfg, path.read_bytes(), transport=httpx.MockTransport(reply))
    assert len(calls) == 6
    assert report['arms']['recorded']['question_statuses'] == {status: 6}
    assert report['arms']['recorded']['dispatched_attempts'] == 6
    assert len(list(owner.glob('terminal-*.bin'))) == 6
    assert reopen(owner) == report
    if outcome != 'valid': assert report['arms']['recorded']['complete_papers'] == 0


def test_raw_writer_ack_precedes_projection(tmp_path, monkeypatch):
    import tools.method_validation.collections_run as runtime
    cfg, path = native(tmp_path)
    actual, events = runtime.publish, []
    def writer(destination, raw):
        if destination.name.startswith('terminal-'):
            events.append('writer-raised')
            actual(destination, raw)
            raise OSError('raw ACK missing after real write')
        actual(destination, raw)
    def projection(*args):
        events.append('projection')
        raise AssertionError('must not project after unacknowledged write')
    monkeypatch.setattr(runtime, 'publish', writer)
    monkeypatch.setattr(runtime, 'project', projection)
    with pytest.raises(OSError, match='ACK missing'):
        run(cfg, path.read_bytes(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response())))
    assert events == ['writer-raised']


@pytest.mark.parametrize('field', ['public_input_consent', 'external_guard'])
def test_live_consent_and_guard_are_both_required_before_owner(tmp_path, field):
    cfg, path = native(tmp_path)
    values = cfg.model_dump()
    values[field] = False if field == 'public_input_consent' else None
    cfg = Config.model_validate(values)
    with pytest.raises(ValueError, match='consent'): prepare(cfg)
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('limit', [('max_nodes', 2), ('max_tree_depth', 1)])
def test_taxonomy_work_is_bounded_before_path_expansion(tmp_path, limit):
    config, _, _, _ = bundle(tmp_path)
    config[limit[0]] = limit[1]
    with pytest.raises(ValueError): prepare(Config.model_validate(config))


def test_native_paper_time_does_not_qualify_unknown_resources(tmp_path):
    cfg, path = native(tmp_path)
    owner, report = run(cfg, path.read_bytes(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response())))
    first = json.loads((owner / 'case-0.json').read_bytes())
    second = json.loads((owner / 'case-1.json').read_bytes())
    assert report['arms']['recorded']['complete_paper_latency_seconds']['n'] == 3
    assert first['telemetry']['elapsed_seconds'] >= 0 and second['telemetry']['elapsed_seconds'] >= 0
    assert report['arms']['recorded']['cold_seconds'] is None
    first['telemetry']['elapsed_seconds'] = -1
    with pytest.raises(ValueError): Result.model_validate(first)
    first['telemetry']['elapsed_seconds'] = True
    with pytest.raises(ValueError): Result.model_validate(first)


def test_paper_time_spans_all_question_and_row_ack_work_not_only_http(tmp_path, monkeypatch):
    import itertools
    import tools.method_validation.collections_run as runtime
    cfg, path = native(tmp_path)
    clock = itertools.count()
    monkeypatch.setattr(runtime, 'monotonic', lambda: float(next(clock)))
    owner, report = run(cfg, path.read_bytes(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response())))
    assert report['arms']['recorded']['complete_paper_latency_seconds']['mean'] == 3
    assert reopen(owner) == report


@pytest.mark.parametrize('mutation', ['negative', 'missing', 'foreign', 'duplicate'])
def test_paper_timing_cannot_move_between_owner_or_completion_states(tmp_path, mutation):
    from tools.method_validation.support import encoded, sha
    cfg, path = native(tmp_path)
    owner, _ = run(cfg, path.read_bytes(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response())))
    timings = json.loads((owner / 'paper-times.json').read_bytes())
    if mutation == 'negative': timings[0]['elapsed_seconds'] = -1
    if mutation == 'missing': timings.pop()
    if mutation == 'foreign': timings[0]['paper_sha256'] = 'f'*64
    if mutation == 'duplicate': timings.append(timings[0])
    (owner / 'paper-times.json').write_bytes(encoded(timings))
    manifest = json.loads((owner / 'manifest.json').read_bytes())
    manifest['files']['paper-times.json'] = sha((owner / 'paper-times.json').read_bytes())
    (owner / 'manifest.json').write_bytes(encoded(manifest))
    (owner / 'ACK').write_text(sha((owner / 'manifest.json').read_bytes()))
    with pytest.raises(ValueError): reopen(owner)


def test_threshold_abstention_preserves_choice_and_does_not_claim_no_match(tmp_path):
    from test_collection_fixtures import bind
    from test_collection_reservations import development_reference
    from tools.method_validation.support import digest
    config, inputs, truth, path = bundle(tmp_path)
    dev = development_reference()
    dev['observed_at'] = '2026-07-15T00:00:00Z'
    inputs['papers'].append(dev)
    config['arms'][0]['abstention_threshold'] = .5
    config['calibration'] = {'split_sha256': digest([(p['paper_id'], p['family'], p['split']) for p in inputs['papers']]),
        'development_families': ['dev-family'], 'method': 'SYNTHETIC predeclared fixture', 'frozen_at': '2026-09-02T00:00:00Z'}
    raw = response('belongs', probabilities={'belongs': .1, 'does_not_belong': .8, 'insufficient_evidence': .1})
    inputs['recordings'][0]['terminal'] = bind(tmp_path, 'abstention.bin', raw)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, report = run(cfg, path.read_bytes())
    row = json.loads((owner / 'case-0.json').read_bytes())
    assert row['status'] == 'threshold_abstention' and row['label'] is None
    assert row['readout']['label'] == 'belongs' and row['readout']['probabilities'] == [.1, .8, .1]
    assert report['arms']['recorded']['complete_papers'] == 2
    assert reopen(owner) == report
