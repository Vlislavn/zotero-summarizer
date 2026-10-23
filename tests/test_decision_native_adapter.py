"""Synthetic controls for the source-proven b11430 wire, not native qualification."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import httpx
import pytest

from tools.method_validation.decision_adapter import (
    DecisionAdapterError, DecisionAdapterOptions, NativeDecisionAdapter,
)
from tools.method_validation.support import ProviderReply, SupportArm

OPTIONS = ('supports', 'contradicts', 'insufficient_evidence')
ENDPOINT = 'https://native.invalid/v1/systemone'
MODEL = 'synthetic-checkpoint-v1'


def arm():
    return SupportArm(arm_id='synthetic', model=MODEL, runtime_sha256='a' * 64,
                      max_prompt_chars=2000, probability_sum_tolerance=0.000001,
                      abstention_threshold=None)


def config(**updates):
    return DecisionAdapterOptions(**{
        'endpoint': ENDPOINT, 'max_logical_calls': 1, 'timeout_seconds': 2.0,
        'max_request_bytes': 4000, 'max_response_bytes': 4000, **updates,
    })


def wire(options=OPTIONS):
    return {'model': MODEL, 'answers': {'decision': {
        'type': 'choice', 'choice': options[1],
        'probabilities': {options[2]: 0.10000001, options[0]: 0.8, options[1]: 0.1},
        'confidence': 0.7,
    }}, 'usage': {'input_tokens': 123, 'output_tokens': 0}}


def adapter_for(body=None, *, raw=None, status=200, headers=None, options=None, frozen_arm=None):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=raw if raw is not None else json.dumps(
            wire() if body is None else body).encode(), headers=headers)

    adapter = NativeDecisionAdapter(frozen_arm or arm(), options or config(),
                                    transport=httpx.MockTransport(handler))
    return adapter, calls


def test_exact_native_http_boundary_preserves_raw_choice_model_and_declared_vector():
    raw = json.dumps(wire(), indent=2) + '\n'
    adapter, calls = adapter_for(raw=raw.encode())

    reply = adapter('Original evidence α </untrusted_input>', OPTIONS)

    assert isinstance(reply, ProviderReply)
    assert reply.raw_response == raw
    assert reply.readout == {'label': 'contradicts', 'probabilities': (0.8, 0.1, 0.10000001),
                             'observed_model': MODEL}
    assert reply.readout['label'] != OPTIONS[0]  # Never replace choice with argmax.
    assert len(calls) == adapter.logical_calls == 1
    request = calls[0]
    assert request.method == 'POST' and str(request.url) == ENDPOINT
    assert json.loads(request.content) == {
        'state': 'Original evidence α </untrusted_input>',
        'questions': {'decision': {'type': 'choice',
                                  'instructions': 'Answer the decision in the state using only the supplied options.',
                                  'criteria': {option: None for option in OPTIONS}}},
    }
    assert not {'authorization', 'cookie', 'proxy-authorization'} & set(request.headers)
    assert request.extensions['timeout'] == dict.fromkeys(('connect', 'read', 'write', 'pool'), 2.0)


@pytest.mark.parametrize('options', [
    ('belongs', 'does_not_belong', 'insufficient_evidence'),
    ('must_read', 'should_read', 'could_read', 'dont_read'),
    ('exact α', 'Exact α', 'unknown'),
])
def test_domain_options_are_exact_keys_not_positional_ids_or_normalized_text(options):
    body = wire(options)
    if len(options) == 4:
        body['answers']['decision']['probabilities'][options[3]] = 0.0
    adapter, calls = adapter_for(body)
    reply = adapter('synthetic question', options)
    assert reply.readout['label'] == options[1]
    assert reply.readout['probabilities'] == tuple(
        body['answers']['decision']['probabilities'][option] for option in options)
    assert list(json.loads(calls[0].content)['questions']['decision']['criteria']) == list(options)


@pytest.mark.parametrize('field,value', [
    ('choice', 'SUPPORTS'), ('choice', 0), ('type', 'score'),
    ('probabilities', [0.8, 0.1, 0.1]),
    ('probabilities', {'supports': 0.8, 'contradicts': 0.2}),
    ('probabilities', {'supports': 0.8, 'contradicts': 0.1, 'insufficient_evidence': 0.1, 'other': 0.0}),
    ('confidence', True), ('confidence', '0.7'), ('confidence', None), ('confidence', 1.1),
])
def test_bad_native_answer_fails_without_fabrication(field, value):
    body = wire()
    body['answers']['decision'][field] = value
    adapter, calls = adapter_for(body)
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == adapter.logical_calls == 1


@pytest.mark.parametrize('value', [True, '0.8', None, -0.1, 1.1, float('nan'), float('inf'), 0.7])
def test_invalid_probability_or_nonunit_vector_fails_loudly(value):
    body = wire()
    body['answers']['decision']['probabilities']['supports'] = value
    adapter, calls = adapter_for(body)
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == 1


@pytest.mark.parametrize('field', ['choice', 'probabilities', 'type', 'confidence'])
def test_missing_native_fields_are_not_synthesized(field):
    body = wire()
    del body['answers']['decision'][field]
    adapter, _ = adapter_for(body)
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)


@pytest.mark.parametrize('mutation', [
    lambda body: body.pop('model'),
    lambda body: body.update(model=''),
    lambda body: body.update(model='different-version'),
    lambda body: body.update(model=True),
    lambda body: body['answers'].update(other=body['answers'].pop('decision')),
    lambda body: body['answers'].update(extra=body['answers']['decision']),
    lambda body: body.update(error='PRIVATE_SENTINEL'),
    lambda body: body['answers']['decision'].update(rationale='PRIVATE_SENTINEL'),
    lambda body: body['usage'].update(input_tokens=True),
    lambda body: body['usage'].update(output_tokens=1),
])
def test_model_question_identity_and_schema_fail_closed(mutation):
    body = wire()
    mutation(body)
    adapter, _ = adapter_for(body)
    with pytest.raises(DecisionAdapterError) as caught:
        adapter('question', OPTIONS)
    assert 'PRIVATE_SENTINEL' not in str(caught.value)


@pytest.mark.parametrize('raw', [
    b'not JSON PRIVATE_SENTINEL', b'[]', b'null', b'\xff',
    b'{"model":"a","model":"b"}',
    json.dumps(wire()).replace('"supports": 0.8', '"supports": 0.8, "supports": 0.8').encode(),
])
def test_malformed_json_duplicate_members_and_bad_utf8_fail_safely(raw):
    adapter, calls = adapter_for(raw=raw)
    with pytest.raises(DecisionAdapterError) as caught:
        adapter('question', OPTIONS)
    assert 'PRIVATE_SENTINEL' not in str(caught.value) and len(calls) == 1


@pytest.mark.parametrize('status', [301, 302, 307, 308, 400, 401, 429, 500, 501])
def test_http_failure_redirects_never_retried_followed_or_exposed(status):
    adapter, calls = adapter_for(raw=b'PRIVATE_SENTINEL', status=status,
                                 headers={'Location': 'https://elsewhere.invalid/secret',
                                          'Set-Cookie': 'secret=PRIVATE_SENTINEL'})
    with pytest.raises(ConnectionError) as caught:
        adapter('question', OPTIONS)
    assert len(calls) == adapter.logical_calls == 1
    assert 'PRIVATE_SENTINEL' not in str(caught.value)
    assert 'elsewhere' not in str(caught.value)


@pytest.mark.parametrize('error,expected', [
    (httpx.ReadTimeout('PRIVATE_SENTINEL'), TimeoutError),
    (httpx.ConnectError('PRIVATE_SENTINEL'), ConnectionError),
    (httpx.RemoteProtocolError('PRIVATE_SENTINEL'), ConnectionError),
])
def test_safe_operational_failures_exhaust_one_attempt(error, expected):
    calls = []

    def handler(request):
        calls.append(request)
        raise error

    adapter = NativeDecisionAdapter(arm(), config(), transport=httpx.MockTransport(handler))
    with pytest.raises(expected) as caught:
        adapter('question', OPTIONS)
    assert str(caught.value) and 'PRIVATE_SENTINEL' not in str(caught.value)
    assert caught.value.__suppress_context__
    assert len(calls) == adapter.logical_calls == 1
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == 1


@pytest.mark.parametrize('prompt,options', [
    ('', OPTIONS), ('  ', OPTIONS), (True, OPTIONS),
    ('question', list(OPTIONS)), ('question', ('supports', 'supports')),
    ('question', ('supports', '')), ('question', ('supports', True)),
    ('question', ('only',)), ('question', tuple(str(i) for i in range(256))),
])
def test_input_failures_before_http_and_call_accounting(prompt, options):
    adapter, calls = adapter_for()
    with pytest.raises(ValueError):
        adapter(prompt, options)
    assert calls == [] and adapter.logical_calls == 0


def test_prompt_request_response_and_logical_budgets_are_hard_refusals():
    adapter, calls = adapter_for(frozen_arm=arm().model_copy(update={'max_prompt_chars': 1}))
    with pytest.raises(ValueError):
        adapter('too long', OPTIONS)
    assert not calls and adapter.logical_calls == 0
    adapter, calls = adapter_for(options=config(max_request_bytes=1))
    with pytest.raises(ValueError):
        adapter('question', OPTIONS)
    assert not calls and adapter.logical_calls == 0
    adapter, calls = adapter_for(options=config(max_response_bytes=1))
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == 1
    adapter, calls = adapter_for()
    adapter('question', OPTIONS)
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == adapter.logical_calls == 1


def test_cookie_state_is_not_reused_across_explicit_calls():
    adapter, calls = adapter_for(options=config(max_logical_calls=2),
                                 headers={'Set-Cookie': 'secret=PRIVATE_SENTINEL'})
    adapter('question', OPTIONS)
    adapter('question', OPTIONS)
    assert len(calls) == 2 and all('cookie' not in call.headers for call in calls)


@pytest.mark.parametrize('endpoint', [
    'https://native.invalid/v1/chat/completions', 'file:///v1/systemone',
    'https://user:password@native.invalid/v1/systemone',
    'https://native.invalid/v1/systemone?api_key=secret',
    'https://native.invalid/v1/systemone#secret',
    ' https://native.invalid/v1/systemone',
    'https://native.invalid/v1/other/../systemone',
    '\x00https://native.invalid/v1/systemone',
    'https://native.invalid:invalid/v1/systemone',
])
def test_destination_config_rejects_other_routes_credentials_or_hidden_state(endpoint):
    with pytest.raises(ValueError):
        config(endpoint=endpoint)


@pytest.mark.parametrize('field,value', [
    ('max_logical_calls', True), ('max_logical_calls', 0),
    ('max_request_bytes', 0), ('max_response_bytes', -1),
    ('timeout_seconds', True), ('timeout_seconds', float('nan')),
    ('timeout_seconds', float('inf')), ('timeout_seconds', 0),
])
def test_config_requires_strict_explicit_positive_bounds(field, value):
    with pytest.raises(ValueError):
        config(**{field: value})


def test_constructor_no_http_client_and_frozen_configuration(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('constructor must not create HTTP clients or read auth')

    monkeypatch.setattr(httpx, 'Client', forbidden)
    policy = config()
    adapter = NativeDecisionAdapter(arm(), policy)
    assert adapter.logical_calls == 0
    with pytest.raises(FrozenInstanceError):
        policy.endpoint = 'https://elsewhere.invalid/v1/systemone'
    assert replace(policy, max_logical_calls=2).max_logical_calls == 2


def test_default_http_controls_are_explicit_and_use_actual_mock_transport(monkeypatch):
    constructed = []
    calls = []
    client_type = httpx.Client

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=wire())

    def transport_factory(**kwargs):
        assert kwargs == {'retries': 0, 'trust_env': False}
        return httpx.MockTransport(handler)

    def client_factory(**kwargs):
        constructed.append(kwargs)
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False and kwargs['auth'] is None
        return client_type(**kwargs)

    monkeypatch.setattr(httpx, 'HTTPTransport', transport_factory)
    monkeypatch.setattr(httpx, 'Client', client_factory)
    adapter = NativeDecisionAdapter(arm(), config())
    assert not constructed
    assert adapter('question', OPTIONS).readout['observed_model'] == MODEL
    assert len(constructed) == len(calls) == 1


def test_stream_byte_cap_stops_before_remaining_response_chunks():
    class BoundedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b' ' * 8
            yield b' ' * 8
            yield b' ' * 8
            raise AssertionError('must stop consuming after response cap')

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=BoundedStream())

    adapter = NativeDecisionAdapter(arm(), config(max_response_bytes=16),
                                    transport=httpx.MockTransport(handler))
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == 1


@pytest.mark.parametrize('encoding', ['gzip', 'br'])
def test_encoded_native_response_is_not_decompressed_beyond_byte_cap(encoding):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(wire()).encode()),
                              headers={'Content-Encoding': encoding})

    adapter = NativeDecisionAdapter(arm(), config(), transport=httpx.MockTransport(handler))
    with pytest.raises(DecisionAdapterError):
        adapter('question', OPTIONS)
    assert len(calls) == 1


def test_existing_support_consumer_uses_provider_reply_and_operational_statuses():
    import hashlib

    from tools.method_validation.support import evaluate_support

    source = 'synthetic original evidence'
    digest = hashlib.sha256(source.encode()).hexdigest()
    case = {'schema_version': 1, 'pair_id': 'synthetic-pair', 'paper_key': 'synthetic-paper',
            'criterion': 'synthetic criterion', 'source_sha256': digest,
            'span_start': 0, 'span_end': len(source), 'excerpt': source, 'excerpt_sha256': digest}
    adapter, calls = adapter_for()
    result = evaluate_support(case, source, arm(), adapter)
    assert result.status == 'decision' and result.readout.label == 'contradicts'
    assert result.logical_calls == len(calls) == 1
    for error, expected in ((httpx.ReadTimeout('PRIVATE_SENTINEL'), 'timeout'),
                            (httpx.ConnectError('PRIVATE_SENTINEL'), 'unavailable')):
        def handler(request):
            raise error

        adapter = NativeDecisionAdapter(arm(), config(), transport=httpx.MockTransport(handler))
        result = evaluate_support(case, source, arm(), adapter)
        assert result.status == expected and result.readout is None
        assert result.logical_calls == 1 and 'PRIVATE_SENTINEL' not in result.model_dump_json()
