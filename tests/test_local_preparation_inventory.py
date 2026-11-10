"""Preparation native terminals are owned, but never inference readiness."""
import httpx
import pytest

from zotero_summarizer.integrations.llm_requests import RequestInventory
from zotero_summarizer.services.setup.load_attempt import SetupAttempt


@pytest.mark.parametrize('operation', ['load', 'unload'])
def test_native_preparation_has_separate_terminal_semantics(operation):
    inventory = RequestInventory('fixture')
    request = inventory.expect('identity', 'fixture:latest', 'http://127.0.0.1:12345/api/generate',
                               local=True, operation=operation)
    request.native_started('native-fixture')
    body = {'model': 'fixture:latest', 'prompt': '', 'stream': False,
            'keep_alive': 0 if operation == 'unload' else 300}
    request.transport_started(httpx.Request('POST', 'http://127.0.0.1:12345/api/generate', json=body))
    request.terminal('fixture:latest', operation, native_id='native-fixture', native_required=True)
    assert inventory.summary()['coherent'] is True
    assert inventory.receipts()[0]['operation'] == operation


def test_generation_cannot_borrow_a_load_terminal():
    inventory = RequestInventory('fixture')
    request = inventory.expect('identity', 'fixture', 'http://127.0.0.1:1/chat/completions', local=True)
    request.transport_started(httpx.Request('POST', 'http://127.0.0.1:1/chat/completions', json={'model': 'fixture'}))
    with pytest.raises(ValueError):
        request.terminal('fixture', 'load')
    assert inventory.summary()['hold'] is True


@pytest.mark.parametrize('extra', [{'prompt': 'Generate something'}, {'messages': []}, {'images': ['bytes']},
                                  {'system': 'Hidden inference'}, {'stream': True}])
def test_preparation_refuses_inference_payload_before_transport(extra):
    inventory = RequestInventory('fixture')
    request = inventory.expect('identity', 'fixture', 'http://127.0.0.1:1/api/generate', local=True, operation='load')
    body = {'model': 'fixture', 'prompt': '', 'stream': False, 'keep_alive': 300, **extra}
    with pytest.raises(ValueError):
        request.transport_started(httpx.Request('POST', 'http://127.0.0.1:1/api/generate', json=body))
    assert request.receipt()['started'] is False
    assert request.receipt()['not_submitted'] is True


def test_only_successful_preparation_cannot_publish_operational():
    attempt = SetupAttempt()

    def prepare_only():
        request = attempt.requests.expect('identity', 'fixture', 'http://127.0.0.1:1/api/generate',
                                          local=True, operation='load')
        request.transport_started(httpx.Request('POST', 'http://127.0.0.1:1/api/generate', json={
            'model': 'fixture', 'prompt': '', 'stream': False, 'keep_alive': 300}))
        request.terminal('fixture', 'load')
        return {'status': 'operational'}

    result = attempt.run(prepare_only, timeout=2)
    assert result['status'] == 'fail' and result['reason_code'] == 'request_inventory_missing'
    assert result['request_completion']['coherent'] is True


def test_inline_presend_refusal_is_positive_no_dispatch_not_uncertainty():
    from zotero_summarizer.integrations.llm_callbacks import CompletionGuard
    from zotero_summarizer.services._adapters import _verification_send

    inventory = RequestInventory('fixture')
    guard = CompletionGuard(expected_model='fixture', request_owner=lambda: inventory.expect(
        'identity', 'fixture', 'http://127.0.0.1:1/chat/completions', local=True))
    guard.begin_request()
    guard.on_chat_model_start({}, [], run_id='native')

    def refuse():
        raise RuntimeError('Fresh resource admission refused')

    try:
        send = _verification_send(guard, None, refuse)
        with pytest.raises(RuntimeError, match='admission refused'):
            send(httpx.Request('POST', 'http://127.0.0.1:1/chat/completions', json={'model': 'fixture'}))
    finally:
        guard.end_request()
    assert inventory.receipts()[0]['not_submitted'] is True
    assert inventory.summary()['ownership'] == 'not_submitted'
    assert inventory.summary()['fence'] is False


def test_unresolved_preparation_is_sticky_not_a_successful_load():
    inventory = RequestInventory('fixture')
    request = inventory.expect('identity', 'fixture', 'http://127.0.0.1:1/api/generate', local=True, operation='load')
    request.transport_started(httpx.Request('POST', 'http://127.0.0.1:1/api/generate', json={
        'model': 'fixture', 'prompt': '', 'stream': False, 'keep_alive': 300}))
    assert inventory.summary()['hold'] is True
    with pytest.raises(ValueError):
        request.terminal('fixture', 'stop')
    assert inventory.summary()['hold'] is True
