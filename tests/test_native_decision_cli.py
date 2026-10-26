"""Real parser -> adapter -> report -> immutable artifact wiring; no live endpoint."""
from pathlib import Path

import httpx
import pytest

from tools.method_validation.__main__ import main
from tools.method_validation import support_run
from tools.method_validation.decision_adapter import NativeDecisionAdapter, payload, prompt
from tools.method_validation.support import SupportCase, encoded, loads, sha
from test_support_fixtures import (
    ENDPOINT, MODEL, SENTINEL, fixture, native_wire, rows_at, validated,
)


def test_actual_cli_assembly_mocktransport_gold_firewall_and_protected_state(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.services.model import claim_checker
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services import _common
    def forbidden(*args, **kwargs):
        raise AssertionError('production baseline construction/write is forbidden')
    monkeypatch.setattr(claim_checker, 'get_claim_checker', forbidden)
    monkeypatch.setattr(factory, 'build_client_for_provider', forbidden)
    monkeypatch.setattr(_common, 'atomic_write', forbidden)
    monkeypatch.setattr(_common, 'write_json_atomic', forbidden)
    cfg, _, case, config_path = fixture(tmp_path, kind='native')
    protected = [tmp_path / 'selected/data' / name for name in (
        'golden.csv', 'corpus_cache.db', 'triage_history.db', 'models/model.joblib', 'labels.json')]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'protected public fixture bytes')
    before = {p: p.read_bytes() for p in protected}
    captured = []
    raw = encoded(native_wire()) + b'\n'
    def handler(request):
        captured.append(request)
        body = loads(request.content)
        assert request.method == 'POST' and str(request.url) == ENDPOINT
        assert set(body) == {'state', 'questions'}
        assert body['state'] == prompt(SupportCase(**case))
        question = body['questions']['decision']
        assert list(question['criteria']) == sorted(('supports', 'contradicts', 'insufficient_evidence'))
        assert all(value is None for value in question['criteria'].values())
        assert SENTINEL.encode() not in request.content
        assert b'human_provenance' not in request.content and b'gold' not in request.content
        assert b'holdout-family' not in request.content and b'public-paper' not in request.content
        assert '&lt;/untrusted_input&gt;' in body['state']
        assert not {'authorization', 'cookie', 'proxy-authorization'} & set(request.headers)
        assert request.extensions['timeout']['read'] <= 120
        return httpx.Response(200, content=raw)
    assert main(['support', '--config', str(config_path), '--run'],
                transport=httpx.MockTransport(handler)) == 0
    owner = tmp_path / 'selected/data/support_comparison/public-attempt'
    assert (owner / 'terminal-0.bin').read_bytes() == raw
    assert len(captured) == 1
    row = rows_at(owner)[0]
    assert row['readout']['usage'] == {'input_tokens': 123, 'output_tokens': 0}
    assert row['telemetry']['dispatched'] is True and row['telemetry']['client_retries'] == 0
    assert row['telemetry']['observed_forward_passes'] is None
    assert row['telemetry']['declared_forward_passes_bound'] == 2
    assert {p: p.read_bytes() for p in protected} == before
    assert support_run.reopen(owner)['winner'] is None
    monkeypatch.setattr(httpx, 'Client', forbidden)
    assert main(['support', '--inspect', str(owner)]) == 0
    assert {p: p.read_bytes() for p in protected} == before
    assert 'retain_baseline' in capsys.readouterr().out


@pytest.mark.parametrize('alias,expected', [
    (b'"\\ud800"', 'invalid_response'), (b'"\\udfff"', 'invalid_response'),
    ('"Valid Ω alias"'.encode('utf-8'), 'model_mismatch'),
])
def test_unicode_alias_failure_is_per_pair_and_preserves_valid_neighbor(tmp_path, alias, expected):
    from test_support_fixtures import update_input

    cfg, bundle, case, _ = fixture(tmp_path, kind='native')
    bundle['cases'].append({**case, 'pair_id': 'neighbor', 'criterion': 'A distinct neighboring criterion'})
    update_input(cfg, bundle)
    invalid = encoded(native_wire()).replace(encoded(MODEL), alias)
    responses = iter((invalid, encoded(native_wire())))
    owner, result = support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=next(responses))))
    native = [row for row in rows_at(owner) if row['arm_id'] == 'native']
    assert [row['status'] for row in native] == [expected, 'decision']
    assert native[0]['support'] is None and native[1]['support'] is True
    assert (owner / native[0]['terminal']).read_bytes() == invalid
    assert native[0]['readout'] is None if expected == 'invalid_response' else native[0]['readout'] is not None
    assert (owner / 'ACK').is_file() and not (owner / 'FAILED').exists()
    assert support_run.reopen(owner) == result


def test_default_cli_is_read_only_and_does_not_construct_http(tmp_path, monkeypatch, capsys):
    cfg, _, _, path = fixture(tmp_path, kind='native')
    def forbidden(*args, **kwargs):
        raise AssertionError('default CLI is admission only')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    assert main(['support', '--config', str(path)]) == 0
    assert not (tmp_path / 'selected').exists()
    assert '"model_calls": 0' in capsys.readouterr().out
    monkeypatch.setenv('ZS_SUPPORT_OVERRIDES', '{"max_prompt_chars":1}')
    with pytest.raises(ValueError, match='cap'):
        main(['support', '--config', str(path)])
    assert not (tmp_path / 'selected').exists()


def malformed_cases():
    for field, value in [('choice', 'foreign'), ('choice', None), ('type', 'score'),
                         ('confidence', True), ('confidence', 1.1), ('confidence', '0.7'),
                         ('confidence', 10 ** 1000)]:
        body = native_wire()
        body['answers']['decision'][field] = value
        yield encoded(body)
    for vector in ([0.8, 0.1, 0.1], {'supports': 0.8},
                   {'supports': 0.8, 'contradicts': 0.1, 'insufficient_evidence': 0.1, 'foreign': 0},
                   {'supports': True, 'contradicts': 0.1, 'insufficient_evidence': 0.1},
                   {'supports': 0.7, 'contradicts': 0.1, 'insufficient_evidence': 0.1},
                   {'supports': -0.1, 'contradicts': 0.1, 'insufficient_evidence': 1.0}):
        body = native_wire()
        body['answers']['decision']['probabilities'] = vector
        yield encoded(body)
    for field in ('choice', 'confidence', 'probabilities', 'type'):
        body = native_wire()
        del body['answers']['decision'][field]
        yield encoded(body)
    for usage in ({'input_tokens': True, 'output_tokens': 0}, {'input_tokens': 1, 'output_tokens': 1},
                  {'input_tokens': 10 ** 1000, 'output_tokens': 0}):
        yield encoded(native_wire(usage=usage))
    yield b'{"model":"a","model":"b"}'
    yield encoded(native_wire()).replace(b'0.8', b'NaN')
    yield encoded(native_wire()).replace(b'0.8', b'Infinity')
    yield b'not JSON terminal bytes\xff'
    yield b'[]'


@pytest.mark.parametrize('raw', list(malformed_cases()))
def test_bad_terminal_retained_through_real_adapter_evaluator_artifact_and_reopen(tmp_path, raw):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=raw)
    owner, result = support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(handler))
    row = rows_at(owner)[0]
    assert row['status'] == 'invalid_response' and row['support'] is None and row['readout'] is None
    assert (owner / row['terminal']).read_bytes() == raw
    assert len(calls) == 1 and result['arms']['native']['labelled_decisions'] == 0
    assert support_run.reopen(owner) == result


@pytest.mark.parametrize('model,status', [('different-model', 'model_mismatch'), (MODEL, 'decision')])
def test_model_alias_exact_and_native_choice_never_replaced_by_argmax(tmp_path, model, status):
    body = native_wire(model=model)
    body['answers']['decision']['choice'] = 'contradicts'
    cfg, _, _, _ = fixture(tmp_path, raw=encoded(body))
    owner, _ = support_run.run(validated(cfg), encoded(cfg))
    row = rows_at(owner)[0]
    assert row['status'] == status
    assert row['readout']['label'] == 'contradicts'
    assert row['readout']['probabilities'] == [0.8, 0.1, 0.1]
    assert row['readout']['observed_model'] == model
    assert row['support'] is (False if status == 'decision' else None)


def test_insufficiency_and_native_selected_probability_abstention_are_distinct(tmp_path):
    body = native_wire()
    body['answers']['decision']['choice'] = 'insufficient_evidence'
    cfg, _, _, _ = fixture(tmp_path, raw=encoded(body))
    owner, _ = support_run.run(validated(cfg), encoded(cfg))
    semantic = rows_at(owner)[0]
    assert semantic['status'] == 'decision' and semantic['support'] is False
    cfg['attempt'] = 'threshold-attempt'
    cfg['arms'][0]['abstention_threshold'] = 0.5
    owner, _ = support_run.run(validated(cfg), encoded(cfg))
    row = rows_at(owner)[0]
    assert row['status'] == 'threshold_abstention' and row['support'] is None
    assert row['readout'] == semantic['readout']


@pytest.mark.parametrize('status', [301, 401, 429, 500, 501])
def test_http_failure_body_retained_no_follow_retry_or_negative_label(tmp_path, status):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=b'raw operational error',
                              headers={'Location': 'https://elsewhere.invalid'})
    owner, _ = support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(handler))
    row = rows_at(owner)[0]
    assert row['status'] == 'unavailable' and row['support'] is None
    assert row['telemetry']['http_status'] == status
    assert (owner / row['terminal']).read_bytes() == b'raw operational error'
    assert len(calls) == 1


def test_partial_timeout_bytes_preserved_and_unknown_resources_null(tmp_path):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    class Partial(httpx.SyncByteStream):
        def __iter__(self):
            yield b'partial raw terminal'
            raise httpx.ReadTimeout('PRIVATE_EXCEPTION_NOT_A_LABEL')
    owner, result = support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Partial())))
    row = rows_at(owner)[0]
    assert row['status'] == 'timeout' and row['support'] is None
    assert row['telemetry']['raw_complete'] is False
    assert (owner / row['terminal']).read_bytes() == b'partial raw terminal'
    assert b'PRIVATE_EXCEPTION' not in (owner / 'pair-0.json').read_bytes()
    assert result['arms']['native']['cost'] is None
    assert support_run.reopen(owner) == result


def test_oversized_terminal_retains_only_bounded_prefix_not_a_semantic_readout(tmp_path):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    cfg['max_response_bytes'] = 16
    class Large(httpx.SyncByteStream):
        def __iter__(self):
            yield b'a' * 8
            yield b'b' * 16
            raise AssertionError('must not consume after cap')
    owner, result = support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Large())))
    row = rows_at(owner)[0]
    assert row['status'] == 'response_too_large' and row['readout'] is None
    assert (owner / row['terminal']).read_bytes() == b'a' * 8 + b'b' * 8
    assert row['telemetry']['raw_complete'] is False
    assert support_run.reopen(owner) == result


def test_encoded_terminal_is_raw_not_implicitly_decompressed(tmp_path):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    raw = b'not actually gzip: preserve original terminal bytes'
    owner, _ = support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(raw), headers={'Content-Encoding': 'gzip'})))
    row = rows_at(owner)[0]
    assert row['status'] == 'encoded_response'
    assert (owner / row['terminal']).read_bytes() == raw


@pytest.mark.parametrize('field,value', [('eligibility', 'unknown'), ('access', 'unknown'),
                                       ('checkpoint_sha256', None), ('context_verified', False)])
def test_unqualified_native_arm_is_visibly_blocked_with_no_client(tmp_path, monkeypatch, field, value):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    cfg['arms'][0][field] = value
    def forbidden(*args, **kwargs):
        raise AssertionError('blocked arm must not construct HTTP')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    owner, result = support_run.run(validated(cfg), encoded(cfg))
    row = rows_at(owner)[0]
    assert row['status'] == 'blocked' and row['telemetry']['reserved'] is False
    assert row['telemetry']['dispatched'] is False and result['winner'] is None


@pytest.mark.parametrize('url', ['https://native.invalid/v1/chat/completions',
    'https://user:secret@native.invalid/v1/systemone', 'https://native.invalid/v1/systemone?key=x',
    'https://native.invalid/v1/systemone#fragment', 'file:///v1/systemone',
    'https://native.invalid/v1/other/../systemone', ' https://native.invalid/v1/systemone'])
def test_destination_rejected_before_client_or_owner(tmp_path, url):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    cfg['arms'][0]['endpoint'] = url
    with pytest.raises(ValueError):
        support_run.run(validated(cfg), encoded(cfg), transport=object())
    assert not (tmp_path / 'selected').exists()


def test_wall_expiration_is_reserved_but_not_dispatched(tmp_path, monkeypatch):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    cfg['max_run_seconds'] = 1
    times = iter((0, 2))
    monkeypatch.setattr(support_run, 'monotonic', lambda: next(times))
    owner, result = support_run.run(validated(cfg), encoded(cfg), transport=object())
    row = rows_at(owner)[0]
    assert row['status'] == 'wall_budget' and row['telemetry']['dispatched'] is False
    assert row['telemetry']['reserved'] is True
    assert loads((owner / 'manifest.json').read_bytes())['reserved_calls'] == 1
    assert support_run.reopen(owner) == result


def test_default_native_http_controls_and_direct_adapter_call_cap(tmp_path, monkeypatch):
    cfg, _, case, _ = fixture(tmp_path, kind='native')
    cfg['max_calls'] = 1
    cfg = validated(cfg)
    calls, retained = [], []
    client_type = httpx.Client
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=encoded(native_wire()))
    def transport_factory(**kwargs):
        assert kwargs == {'retries': 0, 'trust_env': False}
        return httpx.MockTransport(handler)
    def client_factory(**kwargs):
        assert kwargs['auth'] is None and kwargs['trust_env'] is False
        assert kwargs['follow_redirects'] is False
        return client_type(**kwargs)
    monkeypatch.setattr(httpx, 'HTTPTransport', transport_factory)
    monkeypatch.setattr(httpx, 'Client', client_factory)
    adapter = NativeDecisionAdapter(cfg.arms[0], cfg)
    request = payload(SupportCase(**case), cfg)
    reply = adapter(request, retained.append)
    assert reply.raw_response == retained[0] == encoded(native_wire())
    assert reply.status == 'decision' and len(calls) == 1
    with pytest.raises(ValueError, match='budget'):
        adapter(request, retained.append)
    assert len(calls) == 1 and len(retained) == 1


def deadline_case(tmp_path, task):
    from tools.method_validation import collections, collections_run, priority, priority_run
    from test_priority_boundaries import wire as priority_wire
    from test_priority_fixtures import fixture as priority_fixture, write_ref
    from test_collection_native import native as collection_fixture
    from test_collection_fixtures import response, save

    if task == 'support':
        values, _, _, _ = fixture(tmp_path, kind='native')
        return validated(values), support_run, encoded(native_wire()), 'pair', encoded(values)
    if task == 'priority':
        values, _, _ = priority_fixture(tmp_path, native=True)
        inputs = loads(Path(values['inputs']['path']).read_bytes())
        truth = loads(Path(values['gold']['path']).read_bytes())
        inputs['papers'] = inputs['papers'][:1]
        selected = priority.identity(priority.Paper.model_validate(inputs['papers'][0]))
        truth['labels'] = [row for row in truth['labels'] if row['identity'] == selected]
        values['inputs'] = write_ref(tmp_path / 'inputs.json', inputs)
        values['gold'] = write_ref(tmp_path / 'gold.json', truth)
        return priority.Config.model_validate(values), priority_run, encoded(priority_wire()), 'case', encoded(values)
    cfg, _ = collection_fixture(tmp_path)
    values = cfg.model_dump(mode='json')
    inputs, truth = loads(cfg.inputs.path.read_bytes()), loads(cfg.gold.path.read_bytes())
    inputs['papers'] = inputs['papers'][:1]
    inputs['tree']['nodes'] = [node for node in inputs['tree']['nodes'] if node['key'] == 'A']
    selected = collections.identity(collections.Paper.model_validate(inputs['papers'][0]))
    truth['labels'] = [dict(row, memberships=[key for key in row['memberships'] if key == 'A'])
                       for row in truth['labels'] if row['identity'] == selected]
    values = save(tmp_path, values, inputs, truth)
    return collections.Config.model_validate(values), collections_run, response(), 'case', encoded(values)


@pytest.mark.parametrize('task', ['support', 'priority', 'collection'])
@pytest.mark.parametrize('late', [False, True])
def test_full_run_uses_remaining_deadline_not_idle_timeout(tmp_path, monkeypatch, task, late):
    from tools.method_validation import decision_adapter

    cfg, runtime, raw, prefix, raw_config = deadline_case(tmp_path, task)
    remaining = min(cfg.idle_timeout_seconds / 2, cfg.max_run_seconds / 4)
    clock, caller_ticks, consumed, requests = [0.0], [0], [], []
    steps = 4
    interval = remaining * (2 if late else 0.5) / steps

    def caller_clock():
        caller_ticks[0] += 1
        if caller_ticks[0] == 2:
            clock[0] = cfg.max_run_seconds - remaining
        return clock[0]

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(steps):
                clock[0] += interval
                consumed.append(index)
                yield raw[len(raw) * index // steps:len(raw) * (index + 1) // steps]

    def server(request):
        requests.append(request)
        assert 0 < interval < request.extensions['timeout']['read']
        return httpx.Response(200, stream=Trickle())

    monkeypatch.setattr(runtime, 'monotonic', caller_clock)
    monkeypatch.setattr(decision_adapter, 'monotonic', lambda: clock[0])
    owner, report = runtime.run(cfg, raw_config, transport=httpx.MockTransport(server))
    row = loads((owner / f'{prefix}-0.json').read_bytes())
    terminal = (owner / row['terminal']).read_bytes()
    assert row['arm_id'] == cfg.arms[0].arm_id and len(requests) == 1
    assert row['telemetry']['dispatched'] is True and row['telemetry']['attempt'] == 1
    assert row['request_sha256'] == sha(requests[0].content)
    assert row['status'] == ('timeout' if late else 'decision')
    if late:
        assert row['readout'] is None and row['telemetry']['raw_complete'] is False
        assert raw.startswith(terminal) and 0 < len(terminal) < len(raw)
        assert len(consumed) < steps
    else:
        assert row['readout'] is not None and terminal == raw and len(consumed) == steps
    assert runtime.reopen(owner) == report and len(requests) == 1


def test_programmer_error_propagates_and_marks_failed_owner(tmp_path):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    def bug(request):
        raise RuntimeError('programmer error must propagate')
    with pytest.raises(RuntimeError, match='programmer error'):
        support_run.run(validated(cfg), encoded(cfg), transport=httpx.MockTransport(bug))
    owner = tmp_path / 'selected/data/support_comparison/public-attempt'
    assert (owner / 'FAILED').exists()
    with pytest.raises(ValueError, match='failed owner'):
        support_run.reopen(owner)
