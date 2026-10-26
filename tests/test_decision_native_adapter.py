"""Synthetic controls for actual b11430 transport; no native qualification claim."""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from tools.method_validation.decision_adapter import (
    NativeDecisionAdapter, ProviderReply, evaluate_support, payload, project,
)
from tools.method_validation.support import Config, SUPPORT_OPTIONS, admit_support_case, encoded, loads
from test_support_fixtures import ENDPOINT, MODEL, native_env, native_wire


def adapter_for(env, *, arm_updates=None, projector=project, max_elapsed_seconds=None, **policy):
    data = env.cfg.model_dump()
    data.update({'max_calls': 1, **policy})
    if arm_updates is not None:
        data['arms'][0].update(arm_updates)
    env.cfg = Config.model_validate(data)
    return NativeDecisionAdapter(env.cfg.arms[0], env.cfg, transport=env.transport,
                                 projector=projector, max_elapsed_seconds=max_elapsed_seconds)


def test_exact_native_http_boundary_preserves_raw_choice_model_and_declared_vector(native_env):
    wire = native_wire()
    wire['answers']['decision'].update(choice='contradicts', probabilities={
        'insufficient_evidence': 0.10000001, 'supports': 0.8, 'contradicts': 0.1})
    native_env.raw = json.dumps(wire, indent=2).encode() + b'\n'
    adapter = adapter_for(native_env)
    reply = adapter(native_env.request, native_env.retained.append)
    assert isinstance(reply, ProviderReply) and reply.status == 'decision'
    assert reply.raw_response == native_env.raw and native_env.retained == [native_env.raw]
    assert reply.readout == {'label': 'contradicts', 'probabilities': [0.8, 0.1, 0.10000001],
                            'observed_model': MODEL, 'confidence': 0.7,
                            'usage': {'input_tokens': 123, 'output_tokens': 0}}
    assert len(native_env.requests) == adapter.calls == 1
    request = native_env.requests[0]
    assert request.method == 'POST' and str(request.url) == ENDPOINT
    assert request.content == native_env.request
    body = loads(request.content)
    assert set(body) == {'state', 'questions'} and 'α' in body['state']
    assert '&lt;/untrusted_input&gt;' in body['state']
    assert list(body['questions']['decision']['criteria']) == list(native_env.cfg.option_order)
    assert set(body['questions']['decision']['criteria']) == set(SUPPORT_OPTIONS)
    assert not {'authorization', 'cookie', 'proxy-authorization'} & set(request.headers)
    assert request.extensions['timeout'] == dict.fromkeys(
        ('connect', 'read', 'write', 'pool'), native_env.cfg.idle_timeout_seconds)


@pytest.mark.parametrize('domain', ['support', 'priority', 'collection'])
def test_domain_options_are_exact_keys_not_positional_ids_or_normalized_text(native_env, tmp_path, domain):
    from tools.method_validation import collections, collections_run, priority
    from test_priority_boundaries import wire as priority_wire
    from test_priority_fixtures import fixture as priority_fixture
    from test_collection_native import native as collection_fixture
    from test_collection_fixtures import response

    if domain == 'support':
        cfg, request, labels, projector = native_env.cfg, native_env.request, SUPPORT_OPTIONS, project
        raw = encoded(native_wire())
    else:
        root = tmp_path / domain
        root.mkdir()
        if domain == 'priority':
            values, papers, _ = priority_fixture(root, native=True)
            cfg = priority.Config.model_validate(values)
            request = priority.payload(priority.Paper.model_validate(papers[0]), cfg)
            labels, projector, raw = priority.LABELS, priority.project, encoded(priority_wire())
        else:
            cfg, _ = collection_fixture(root)
            request = next(iter(collections_run.prepare(cfg)[6].values()))
            labels, projector, raw = collections.LABELS, collections.project, response()
    captured, retained = [], []

    def handler(sent):
        captured.append(sent)
        return httpx.Response(200, content=raw)

    adapter = NativeDecisionAdapter(cfg.arms[0], cfg, projector=projector, transport=httpx.MockTransport(handler))
    reply = adapter(request, retained.append)
    answer = loads(raw)['answers']['decision']
    assert reply.status == 'decision' and reply.readout['label'] == answer['choice']
    assert tuple(reply.readout['probabilities']) == tuple(answer['probabilities'][key] for key in labels)
    assert list(loads(captured[0].content)['questions']['decision']['criteria']) == list(cfg.option_order)
    assert captured[0].content == request and retained == [raw]


@pytest.mark.parametrize('field,value', [
    ('choice', 'SUPPORTS'), ('choice', 0), ('type', 'score'),
    ('probabilities', [0.8, 0.1, 0.1]),
    ('probabilities', {'supports': 0.8, 'contradicts': 0.2}),
    ('probabilities', {'supports': 0.8, 'contradicts': 0.1, 'insufficient_evidence': 0.1, 'other': 0.0}),
    ('confidence', True), ('confidence', '0.7'), ('confidence', None), ('confidence', 1.1),
])
def test_bad_native_answer_fails_without_fabrication(native_env, field, value):
    wire = native_wire()
    wire['answers']['decision'][field] = value
    native_env.raw = encoded(wire)
    adapter = adapter_for(native_env)
    reply = adapter(native_env.request, native_env.retained.append)
    assert reply.status == 'invalid_response' and reply.readout is None
    assert reply.raw_response == native_env.raw and native_env.retained == [native_env.raw]
    assert len(native_env.requests) == adapter.calls == 1


@pytest.mark.parametrize('value', [True, '0.8', None, -0.1, 1.1, float('nan'), float('inf'), 0.7])
def test_invalid_probability_or_nonunit_vector_fails_loudly(native_env, value):
    wire = native_wire()
    wire['answers']['decision']['probabilities']['supports'] = value
    native_env.raw = json.dumps(wire, allow_nan=True).encode()
    adapter = adapter_for(native_env)
    reply = adapter(native_env.request, native_env.retained.append)
    assert reply.status == 'invalid_response' and reply.readout is None
    assert len(native_env.requests) == 1 and native_env.retained == [native_env.raw]


@pytest.mark.parametrize('field', ['choice', 'probabilities', 'type', 'confidence'])
def test_missing_native_fields_are_not_synthesized(native_env, field):
    wire = native_wire()
    del wire['answers']['decision'][field]
    native_env.raw = encoded(wire)
    reply = adapter_for(native_env)(native_env.request, native_env.retained.append)
    assert reply.status == 'invalid_response' and reply.readout is None
    assert native_env.retained == [native_env.raw]


@pytest.mark.parametrize('mutation,expected', [
    (lambda body: body.pop('model'), 'invalid_response'),
    (lambda body: body.update(model=''), 'invalid_response'),
    (lambda body: body.update(model='different-version'), 'model_mismatch'),
    (lambda body: body.update(model=True), 'invalid_response'),
    (lambda body: body['answers'].update(other=body['answers'].pop('decision')), 'invalid_response'),
    (lambda body: body['answers'].update(extra=body['answers']['decision']), 'invalid_response'),
    (lambda body: body.update(error='PRIVATE_SENTINEL'), 'invalid_response'),
    (lambda body: body['answers']['decision'].update(rationale='PRIVATE_SENTINEL'), 'invalid_response'),
    (lambda body: body['usage'].update(input_tokens=True), 'invalid_response'),
    (lambda body: body['usage'].update(output_tokens=1), 'invalid_response'),
])
def test_model_question_identity_and_schema_fail_closed(native_env, mutation, expected):
    wire = native_wire()
    mutation(wire)
    native_env.raw = encoded(wire)
    reply = adapter_for(native_env)(native_env.request, native_env.retained.append)
    assert reply.status == expected and native_env.retained == [native_env.raw]
    assert (reply.readout is not None) is (expected == 'model_mismatch')
    assert 'PRIVATE_SENTINEL' not in json.dumps(reply.telemetry)


@pytest.mark.parametrize('raw', [
    b'not JSON PRIVATE_SENTINEL', b'[]', b'null', b'\xff', b'{"model":"a","model":"b"}',
    encoded(native_wire()).replace(b'"supports": 0.8', b'"supports": 0.8, "supports": 0.8'),
])
def test_malformed_json_duplicate_members_and_bad_utf8_fail_safely(native_env, raw):
    native_env.raw = raw
    reply = adapter_for(native_env)(native_env.request, native_env.retained.append)
    assert reply.status == 'invalid_response' and reply.readout is None
    assert 'PRIVATE_SENTINEL' not in json.dumps(reply.telemetry)
    assert len(native_env.requests) == 1 and native_env.retained == [raw]


@pytest.mark.parametrize('status', [301, 302, 307, 308, 400, 401, 429, 500, 501])
def test_http_failure_redirects_never_retried_followed_or_exposed(native_env, status):
    native_env.raw, native_env.status = b'PRIVATE_SENTINEL', status
    native_env.headers = {'Location': 'https://elsewhere.invalid/secret', 'Set-Cookie': 'secret=PRIVATE_SENTINEL'}
    adapter = adapter_for(native_env)
    reply = adapter(native_env.request, native_env.retained.append)
    assert reply.status == 'unavailable' and reply.readout is None
    assert len(native_env.requests) == adapter.calls == 1
    assert native_env.retained == [native_env.raw]
    assert 'PRIVATE_SENTINEL' not in json.dumps(reply.telemetry)
    assert 'elsewhere' not in json.dumps(reply.telemetry)


@pytest.mark.parametrize('error,expected', [
    (httpx.ReadTimeout('PRIVATE_SENTINEL'), 'timeout'),
    (httpx.ConnectError('PRIVATE_SENTINEL'), 'unavailable'),
    (httpx.RemoteProtocolError('PRIVATE_SENTINEL'), 'unavailable'),
])
def test_safe_operational_failures_exhaust_one_attempt(native_env, error, expected):
    native_env.error = error
    adapter = adapter_for(native_env)
    reply = adapter(native_env.request, native_env.retained.append)
    assert reply.status == expected and reply.readout is None and reply.raw_response == b''
    assert 'PRIVATE_SENTINEL' not in json.dumps(reply.telemetry)
    assert len(native_env.requests) == adapter.calls == 1
    with pytest.raises(ValueError):
        adapter(native_env.request, native_env.retained.append)
    assert len(native_env.requests) == 1


@pytest.mark.parametrize('owner,updates', [
    ('case', {'criterion': ''}), ('case', {'criterion': '  '}), ('case', {'criterion': True}),
    ('config', {'option_order': ('supports', 'supports')}),
    ('config', {'option_order': ('supports', '')}), ('config', {'option_order': ('supports', True)}),
    ('config', {'option_order': ('only',)}),
    ('config', {'option_order': tuple(str(i) for i in range(256))}),
])
def test_input_failures_before_http_and_call_accounting(native_env, owner, updates):
    adapter = adapter_for(native_env)
    with pytest.raises(ValueError):
        if owner == 'case':
            case = admit_support_case({**native_env.case.model_dump(), **updates}, native_env.original)
            adapter(payload(case, native_env.cfg), native_env.retained.append)
        else:
            cfg = Config.model_validate({**native_env.cfg.model_dump(), **updates})
            adapter(payload(native_env.case, cfg), native_env.retained.append)
    assert native_env.requests == native_env.retained == [] and adapter.calls == 0


def test_prompt_request_response_and_logical_budgets_are_hard_refusals(native_env):
    original = native_env.cfg
    adapter = adapter_for(native_env, max_prompt_chars=1)
    with pytest.raises(ValueError):
        payload(native_env.case, native_env.cfg)
    assert native_env.requests == [] and adapter.calls == 0
    native_env.cfg = original
    adapter = adapter_for(native_env, max_request_bytes=1)
    with pytest.raises(ValueError):
        adapter(native_env.request, native_env.retained.append)
    assert native_env.requests == [] and adapter.calls == 0
    native_env.cfg = original
    adapter = adapter_for(native_env, max_request_bytes=len(native_env.request), max_response_bytes=1)
    assert adapter(native_env.request, native_env.retained.append).status == 'response_too_large'
    assert native_env.retained[-1] == native_env.raw[:1] and len(native_env.requests) == 1
    with pytest.raises(ValueError):
        adapter(native_env.request, native_env.retained.append)
    assert len(native_env.requests) == adapter.calls == 1


def test_cookie_state_is_not_reused_across_explicit_calls(native_env):
    native_env.headers = {'Set-Cookie': 'secret=PRIVATE_SENTINEL'}
    adapter = adapter_for(native_env, max_calls=2)
    adapter(native_env.request, native_env.retained.append)
    adapter(native_env.request, native_env.retained.append)
    assert len(native_env.requests) == 2 and all('cookie' not in call.headers for call in native_env.requests)


@pytest.mark.parametrize('endpoint', [
    'https://native.invalid/v1/chat/completions', 'file:///v1/systemone',
    'https://user:password@native.invalid/v1/systemone',
    'https://native.invalid/v1/systemone?api_key=secret', 'https://native.invalid/v1/systemone#secret',
    ' https://native.invalid/v1/systemone', 'https://native.invalid/v1/other/../systemone',
    '\x00https://native.invalid/v1/systemone', 'https://native.invalid:invalid/v1/systemone',
])
def test_destination_config_rejects_other_routes_credentials_or_hidden_state(native_env, endpoint):
    with pytest.raises(ValueError):
        adapter_for(native_env, arm_updates={'endpoint': endpoint})
    assert native_env.requests == native_env.retained == []


@pytest.mark.parametrize('field,value', [
    ('max_calls', True), ('max_calls', 0), ('max_request_bytes', 0), ('max_response_bytes', -1),
    ('idle_timeout_seconds', True), ('idle_timeout_seconds', float('nan')),
    ('idle_timeout_seconds', float('inf')), ('idle_timeout_seconds', 0),
])
def test_config_requires_strict_explicit_positive_bounds(native_env, field, value):
    with pytest.raises(ValueError):
        adapter_for(native_env, **{field: value})
    assert native_env.requests == []


def test_constructor_no_http_client_and_frozen_configuration(native_env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('constructor must not create HTTP clients or read auth')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    adapter = adapter_for(native_env)
    assert adapter.calls == 0
    with pytest.raises(ValidationError):
        native_env.cfg.arms[0].endpoint = 'https://elsewhere.invalid/v1/systemone'
    assert Config.model_validate({**native_env.cfg.model_dump(), 'max_calls': 2}).max_calls == 2


def test_default_http_controls_are_explicit_and_use_actual_mock_transport(native_env, monkeypatch):
    constructed = []
    actual_client = httpx.Client

    def handler(request):
        native_env.requests.append(request)
        return httpx.Response(200, content=native_env.raw)

    def transport_factory(**kwargs):
        assert kwargs == {'retries': 0, 'trust_env': False}
        return httpx.MockTransport(handler)

    def client_factory(**kwargs):
        constructed.append(kwargs)
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False and kwargs['auth'] is None
        return actual_client(**kwargs)

    monkeypatch.setattr(httpx, 'HTTPTransport', transport_factory)
    monkeypatch.setattr(httpx, 'Client', client_factory)
    adapter = NativeDecisionAdapter(native_env.cfg.arms[0], native_env.cfg)
    assert constructed == []
    assert adapter(native_env.request, native_env.retained.append).readout['observed_model'] == MODEL
    assert len(constructed) == len(native_env.requests) == 1


def test_stream_byte_cap_stops_before_remaining_response_chunks(native_env):
    class BoundedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b' ' * 8
            yield b' ' * 8
            yield b' ' * 8
            raise AssertionError('must stop consuming after response cap')

    def handler(request):
        native_env.requests.append(request)
        return httpx.Response(200, stream=BoundedStream())

    native_env.transport = httpx.MockTransport(handler)
    adapter = adapter_for(native_env, max_response_bytes=16)
    reply = adapter(native_env.request, native_env.retained.append)
    assert reply.status == 'response_too_large' and reply.raw_response == b' ' * 16
    assert len(native_env.requests) == 1 and native_env.retained == [reply.raw_response]


@pytest.mark.parametrize('encoding', ['gzip', 'br'])
def test_encoded_native_response_is_not_decompressed_beyond_byte_cap(native_env, encoding):
    def handler(request):
        native_env.requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(native_env.raw), headers={'Content-Encoding': encoding})

    native_env.transport = httpx.MockTransport(handler)
    reply = adapter_for(native_env)(native_env.request, native_env.retained.append)
    assert reply.status == 'encoded_response' and reply.readout is None
    assert reply.raw_response == native_env.raw and native_env.retained == [native_env.raw]
    assert len(native_env.requests) == 1


def test_existing_support_consumer_uses_provider_reply_and_operational_statuses(native_env):
    def consume():
        return evaluate_support(native_env.case, native_env.cfg.arms[0], native_env.cfg,
                                native_env.retained.append, transport=native_env.transport)
    result = consume()
    assert result.status == 'decision' and result.readout['label'] == 'supports'
    assert result.telemetry['attempt'] == len(native_env.requests) == 1
    for error, expected in ((httpx.ReadTimeout('PRIVATE_SENTINEL'), 'timeout'),
                            (httpx.ConnectError('PRIVATE_SENTINEL'), 'unavailable')):
        native_env.error = error
        before = len(native_env.requests)
        result = consume()
        assert result.status == expected and result.readout is None
        assert result.telemetry['attempt'] == 1 and len(native_env.requests) == before + 1
        assert 'PRIVATE_SENTINEL' not in json.dumps(result.telemetry)


def test_raw_retainer_failure_precedes_projection_even_after_owned_write(native_env, tmp_path):
    events = []

    def retain(raw):
        (tmp_path / 'raw.bin').write_bytes(raw)
        events.append('write-then-raise')
        raise OSError('raw retention did not acknowledge')

    def forbidden(*args):
        events.append('project')
        raise AssertionError('unacknowledged bytes are not projection authority')

    adapter = adapter_for(native_env, projector=forbidden)
    with pytest.raises(OSError):
        adapter(native_env.request, retain)
    assert (tmp_path / 'raw.bin').read_bytes() == native_env.raw
    assert events == ['write-then-raise'] and len(native_env.requests) == adapter.calls == 1


@pytest.mark.parametrize('raw_request', ['α', bytearray(b'x'), [b'x'], None, True])
def test_direct_request_type_refuses_before_dispatch_and_accounting(native_env, raw_request):
    adapter = adapter_for(native_env, max_request_bytes=1)
    with pytest.raises((ValueError, TypeError)):
        adapter(raw_request, native_env.retained.append)
    assert native_env.requests == native_env.retained == [] and adapter.calls == 0


@pytest.mark.parametrize('kind', ['timeout', 'remaining'])
@pytest.mark.parametrize('factor', [0.5, 2])
def test_finite_direct_time_budgets_preserve_configured_caps(native_env, kind, factor):
    value = (native_env.cfg.idle_timeout_seconds if kind == 'timeout' else native_env.cfg.max_run_seconds) * factor
    adapter = adapter_for(native_env, max_elapsed_seconds=value if kind == 'remaining' else None)
    reply = adapter(native_env.request, native_env.retained.append,
                    **({'timeout': value} if kind == 'timeout' else {}))
    assert reply.status == 'decision' and adapter.calls == len(native_env.requests) == 1
    expected = min(value, native_env.cfg.idle_timeout_seconds) if kind == 'timeout' else native_env.cfg.idle_timeout_seconds
    assert native_env.requests[0].extensions['timeout']['read'] == expected
    assert native_env.retained == [native_env.raw]


@pytest.mark.parametrize('kind', ['timeout', 'remaining'])
@pytest.mark.parametrize('value', [True, False, 0, -1, float('nan'), float('inf'), 'invalid'])
def test_direct_time_budget_refuses_before_dispatch_and_accounting(native_env, kind, value):
    adapter = adapter_for(native_env, max_elapsed_seconds=value if kind == 'remaining' else None)
    with pytest.raises((ValueError, TypeError)):
        adapter(native_env.request, native_env.retained.append, **({'timeout': value} if kind == 'timeout' else {}))
    assert native_env.requests == native_env.retained == [] and adapter.calls == 0


@pytest.mark.parametrize('retain', [None, False, object()])
def test_noncallable_retainer_refuses_before_any_request(native_env, retain):
    adapter = adapter_for(native_env)
    with pytest.raises((ValueError, TypeError)):
        adapter(native_env.request, retain)
    assert native_env.requests == [] and adapter.calls == 0
