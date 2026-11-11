"""Actual completion-length → review pipeline refusal and precise call-origin identity."""
import pytest
from langchain_core.outputs import Generation, LLMResult

from test_digest_verification import PAPER, _Verifier, _digest
from zotero_summarizer.integrations.llm_callbacks import CompletionGuard, LLMOutputTruncated
from zotero_summarizer.services.library._review_attempt import Attempt
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def _exhaust_output():
    CompletionGuard().on_llm_end(LLMResult(
        generations=[[Generation(text='incomplete JSON', generation_info={'finish_reason': 'length'})]],
        llm_output={'token_usage': {'prompt_tokens': 16217, 'completion_tokens': 16384}},
    ))


def test_correction_length_is_not_a_generic_generation_failure_or_an_accepted_digest():
    class Generator:
        calls = 0

        def pydantic_prompt(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _digest()
            _exhaust_output()

    generator = Generator()
    verifier = _Verifier(supported=False)
    audit = {}
    with pytest.raises(LLMOutputTruncated), Attempt() as attempt:
        assess_digest(title='Synthetic paper', full_text=PAPER, config=_default_goals_config(),
                      llm=generator, verifier_llm=verifier, verification_audit=audit)
    assert generator.calls == 2 and verifier.calls == 2  # + first (no-reasoning) repair attempt
    assert audit == {}
    assert attempt.metadata()['stage_counts']['generator:parsed_response'] == 1
    assert attempt.metadata()['stage_counts']['verifier:parsed_response'] == 1
    assert 'correction:parsed_response' not in attempt.metadata()['stage_counts']
    terminal = attempt.metadata()['events'][-1]['identity']
    assert terminal['code'] == 'review_output_truncated'
    assert terminal['stage'] == 'correction'
    assert terminal['reason_code'] == 'request_output_truncated'
    assert 'output' in terminal['recovery'].lower() and 'thinking' in terminal['recovery'].lower()
    assert 'incomplete JSON' not in str(attempt.metadata())


@pytest.mark.parametrize('call_stage, expected_stage', [('generator', 'generation'), ('verifier', 'verification')])
def test_truncation_keeps_distinct_generation_and_verification_origins(call_stage, expected_stage):
    from zotero_summarizer.services.library._review_attempt import observed_client

    class Client:
        def prompt(self, _prompt):
            _exhaust_output()

    with pytest.raises(LLMOutputTruncated), Attempt() as attempt:
        observed_client(Client(), call_stage).prompt('Synthetic nonprivate control')
    terminal = attempt.metadata()['events'][-1]['identity']
    assert terminal['code'] == 'review_output_truncated' and terminal['stage'] == expected_stage
