import httpx
import pytest

from test_digest_verification import PAPER, _Verifier, _digest
from test_review_source_boundary import _Generator
from zotero_summarizer.services.library._review_attempt import Attempt
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


@pytest.mark.parametrize('status, recovery_word', [(401, 'authorization'), (404, 'protocol'), (429, 'server status'), (500, 'server status')])
def test_real_generation_http_failure_retains_safe_cause_without_connection_repair_guess(status, recovery_word):
    from zotero_summarizer.api.errors import APIError

    request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
    response = httpx.Response(status, request=request, json={'error': 'sensitive-provider-body-do-not-disclose'})

    class FailedGenerator:
        def pydantic_prompt(self, **_kwargs):
            raise httpx.HTTPStatusError('sensitive SDK/provider prose', request=request, response=response)

    with pytest.raises(APIError) as raised, Attempt() as attempt:
        assess_digest(title='Synthetic paper', full_text=PAPER, config=_default_goals_config(),
                      llm=FailedGenerator(), verifier_llm=_Verifier())
    assert raised.value.error == 'review_provider_unavailable'
    assert f'HTTP {status}' in raised.value.message
    assert recovery_word in raised.value.details['recovery'].lower()
    assert raised.value.details['reason_code'] == 'inference_http_error'
    failure = attempt.metadata()['events'][-1]['identity']
    assert failure['http_status'] == status and failure['reason_code'] == 'inference_http_error'
    assert 'Restore the model connection' not in failure['recovery']
    assert 'sensitive' not in str(attempt.metadata()) + str(raised.value.details) + str(raised.value)


@pytest.mark.parametrize('error, code', [(httpx.ReadTimeout('private timeout prose'), 'inference_timeout'),
                                       (httpx.ConnectError('private transport prose'), 'inference_transport_error')])
def test_generation_transport_failures_do_not_invent_http_status(error, code):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.library._review_attempt import observed_client

    class FailedClient:
        def prompt(self, _prompt):
            raise error

    with pytest.raises(APIError) as raised, Attempt() as attempt:
        observed_client(FailedClient(), 'generator').prompt('Synthetic prompt')
    assert raised.value.details['reason_code'] == code
    assert 'http_status' not in raised.value.details
    assert attempt.metadata()['events'][-1]['identity']['reason_code'] == code
    assert 'private' not in str(attempt.metadata()) + str(raised.value)


def test_metadata_default_records_hashes_without_prompts_or_values():
    with Attempt() as attempt:
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(),
                      llm=_Generator(_digest()), verifier_llm=_Verifier())
    meta = attempt.metadata()
    assert meta['stage_counts']['generator:prompt'] == 1
    assert meta['stage_counts']['verifier:parsed_response'] >= 1
    assert PAPER not in str(meta)
    original = next(event for event in meta['events'] if event['stage'] == 'original')
    assert original['characters'] == len(PAPER)
    assert all('decoded_value' not in event for event in attempt.events)


def test_correction_is_separate_and_403_occurrence_does_not_authorize_access_claim():
    import pytest
    from zotero_summarizer.services.library._digest_verification import DigestSourceRejected

    source = PAPER + ' Appendix identifier 403.'
    bad = _digest().model_copy(update={'read_why': 'Full text is inaccessible (HTTP 403 from MDPI);'})
    generator = _Generator(bad)
    with Attempt(capture=True) as attempt:
        with pytest.raises(DigestSourceRejected, match='unsupported fields'):
            assess_digest(title='Paper', full_text=source, config=_default_goals_config(),
                          llm=generator, verifier_llm=_Verifier(supported=False))
    assert attempt.metadata()['stage_counts']['correction:prompt'] == 2  # #191: no-reasoning attempt, then one escalation
    assert len(generator.prompts) == 2  # only the escalation uses the digest client


def test_capture_freezes_decoded_return_values():
    value = {'checks': [{'supported': True}]}
    attempt = Attempt(capture=True)
    attempt.record('verifier', value, kind='parsed_response')
    value['checks'][0]['supported'] = False
    assert attempt.events[0]['decoded_value']['checks'][0]['supported'] is True


def test_explicit_capture_separates_original_generation_and_decoded_values(tmp_path):
    with Attempt(capture=True) as attempt:
        assess_digest(title='Paper', full_text=PAPER, config=_default_goals_config(),
                      llm=_Generator(_digest()), verifier_llm=_Verifier())
    assert any(event['stage'] == 'original' and event['decoded_value'] == PAPER for event in attempt.events)
    assert any(event['stage'] == 'verifier' and event['kind'] == 'parsed_response' for event in attempt.events)
    path = attempt.save(tmp_path / 'capture')
    assert 'not-raw-transport' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
