from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.outputs import Generation, LLMResult

from zotero_summarizer.integrations.llm_callbacks import CompletionGuard, LLMOutputTruncated


def test_parallel_usage_raw_and_truncation_are_observed_once_per_provider_result():
    from zotero_summarizer.services.library._interpretation_trace import Trace

    class Provider:
        def prompt(self, prompt):
            response = LLMResult(generations=[[Generation(text=prompt, generation_info={'finish_reason': 'length'})],
                                             [Generation(text='second decoded generation')]],
                                 llm_output={'model_name': 'synthetic-provider-model',
                                             'token_usage': {'prompt_tokens': 11, 'completion_tokens': 7}})
            CompletionGuard().on_llm_end(response)

    trace = Trace(max_calls=2)

    def invoke(index):
        with pytest.raises(LLMOutputTruncated):
            trace.invoke(Provider(), 'prompt', 'raw-' + str(index), {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(invoke, range(2))) == [None, None]
    assert len(trace.calls) == 2
    assert all(row['status'] == 'failed' and row['failure_type'] == 'LLMOutputTruncated' for row in trace.calls)
    assert all(row['seconds'] >= 0 and len(row['completions']) == 1 for row in trace.calls)
    assert sum(row['completions'][0]['usage']['prompt_tokens'] for row in trace.calls) == 22
    assert {row['completions'][0]['raw'][0][0]['text'] for row in trace.calls} == {'raw-0', 'raw-1'}
    assert all(len(row['completions'][0]['raw']) == 2 for row in trace.calls)
    CompletionGuard().on_llm_end(LLMResult(generations=[[Generation(text='outside observation')]]))
    assert len(trace.calls) == 2
    assert all(len(row['completions']) == 1 for row in trace.calls)


def test_decoded_observation_is_a_snapshot_not_a_mutable_provider_alias():
    from zotero_summarizer.services.library._interpretation_trace import Trace
    value = {'original': ['provider output']}

    class Provider:
        def prompt(self, prompt):
            return value

    trace = Trace(max_calls=1)
    trace.invoke(Provider(), 'prompt', 'snapshot', {})
    value['original'].clear()
    assert trace.calls[0]['decoded'] == {'original': ['provider output']}


def test_missing_usage_stays_missing_and_call_budget_refuses_before_provider():
    from zotero_summarizer.services.library._interpretation_trace import Trace

    class Provider:
        def __init__(self):
            self.calls = 0

        def prompt(self, prompt):
            self.calls += 1
            CompletionGuard().on_llm_end(LLMResult(generations=[[Generation(text='actual decoded output')]]))
            return 'actual decoded output'

    trace, provider = Trace(max_calls=1), Provider()
    assert trace.invoke(provider, 'prompt', 'first prompt', {}) == 'actual decoded output'
    assert trace.calls[0]['completions'][0]['usage'] is None
    assert trace.calls[0]['completions'][0]['model'] is None
    with pytest.raises(ValueError, match='budget exhausted'):
        trace.invoke(provider, 'prompt', 'second prompt', {})
    assert provider.calls == 1
