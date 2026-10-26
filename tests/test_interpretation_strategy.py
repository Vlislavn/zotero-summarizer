"""Explicit experiment dispatch through the existing rank/prefix/map-reduce owners."""
import builtins
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, inputs, run_review
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.interpretation import InterpretationDigest
from zotero_summarizer.services.library import _map_reduce, interpretation
from zotero_summarizer.services.library._digest_verification import DigestVerifierUnavailable
from zotero_summarizer.services.library._interpretation_trace import Trace

STRATEGIES = ['rank', 'prefix', 'map_reduce', 'direct_map_reduce']


def _run(client, strategy, **kwargs):
    return run_review(client, strategy='map_reduce' if strategy == 'direct_map_reduce' else strategy, **kwargs)


@pytest.mark.parametrize('strategy', STRATEGIES)
@pytest.mark.parametrize('format_arg', [None, {}, {'type': 'json_object'}])
def test_explicit_collector_reaches_native_assessment_unchanged_with_original_evidence(monkeypatch, strategy, format_arg):
    # The direct-map branch is the actual map_reduce_digest called by current experiment dispatch.
    direct = Mock(wraps=_map_reduce.map_reduce_digest)
    monkeypatch.setattr(_map_reduce, 'map_reduce_digest', direct)
    original = interpretation.digest_for_strategy

    def with_format(*args, **kwargs):
        return original(*args, **kwargs, response_format=format_arg)

    monkeypatch.setattr(interpretation, 'digest_for_strategy', with_format)
    client = ScriptedClient()
    result = _run(client, strategy)
    assert interpretation.qualify(result)['items'] == [CARD]
    assert interpretation.selected_original(result) == TEXT
    assert result['digest']['basis'] == ('map_reduce' if 'map_reduce' in strategy else 'full_text')
    assert direct.call_count == (1 if 'map_reduce' in strategy else 0)
    generated = [(model, kwargs) for _, model, kwargs in client.calls if model is not None and issubclass(model, PaperDigest)]
    assert len(generated) == 1 and generated[0][0] is InterpretationDigest
    if format_arg:
        assert 'interpretation' in generated[0][1]['response_format']['json_schema']['schema']['properties']
    else:
        assert generated[0][1] == {}
    assert result['verification_audit']['source']['original_characters'] == len(TEXT)
    for passage in result['verification_audit']['passages']:
        span = passage['selected_span']
        assert passage['text'] == TEXT[span['start']:span['end']]


@pytest.mark.parametrize('strategy', STRATEGIES)
@pytest.mark.parametrize('format_arg', [None, {}, {'type': 'json_object'}])
def test_off_preserves_native_kwargs_prompt_model_calls_and_no_optional_import_io(monkeypatch, strategy, format_arg):
    paper, config, budget = inputs()
    config.quality_review.chunk_strategy = 'map_reduce' if 'map_reduce' in strategy else strategy
    baseline = ScriptedClient(cards=False)
    expected = _map_reduce.digest_for_strategy(paper['title'], TEXT, config, map_llm=baseline, reduce_llm=baseline,
                                              budget=budget, response_format=format_arg)
    client = ScriptedClient(cards=False)
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert 'interpretation' not in name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    actual = _map_reduce.digest_for_strategy(paper['title'], TEXT, config, map_llm=client, reduce_llm=client,
                                            budget=budget, response_format=format_arg)
    assert actual.model_dump() == expected.model_dump() and client.calls == baseline.calls
    assert all(model is PaperDigest for _, model, _ in client.calls if model is not None and issubclass(model, PaperDigest))


@pytest.mark.parametrize('strategy', STRATEGIES)
@pytest.mark.parametrize('source', ['', ' \n\t'])
def test_source_admission_failure_clears_prior_collector_before_any_native_call(strategy, source):
    client = ScriptedClient()
    paper, _, _ = inputs()
    paper.update(text=source, content={'full_text': source})
    with pytest.raises(ValueError):
        _run(client, strategy, paper=paper)
    assert client.calls == []


@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('sub_concurrency', [1, 2])
@pytest.mark.parametrize('failure', ['empty', 'exception'])
def test_partial_map_failure_clears_prior_collector_without_reduction_or_verification(direct, sub_concurrency, failure):
    client = ScriptedClient()
    mapper = ScriptedClient()
    mapper.prompt = Mock(side_effect=['Generation context only.', '' if failure == 'empty' else RuntimeError('map failed'),
                                      *(['Generation context only.'] * 20)])
    budget = _map_reduce.ChunkBudget(1000, 1000, sub_concurrency)
    with pytest.raises(ValueError if failure == 'empty' else RuntimeError):
        _run(client, 'direct_map_reduce' if direct else 'map_reduce', mapper=mapper, budget=budget)
    assert mapper.prompt.call_count >= 2
    assert client.calls == [] and mapper.calls == []


@pytest.mark.parametrize('strategy', STRATEGIES)
def test_native_parse_retry_and_base_correction_retain_only_final_collector_and_original_source(strategy):
    client = ScriptedClient(cards=False, responses=['', PAYLOAD | {'tldr': 'Unreported 99999%',
        'interpretation': [dict(CARD, rationale='First attempt')]}, PAYLOAD | {'interpretation': [CARD]}])
    result = _run(client, strategy)
    assert interpretation.qualify(result)['items'] == [CARD]
    assert interpretation.selected_original(result) == TEXT
    generated = [model for _, model, _ in client.calls if model is not None and issubclass(model, PaperDigest)]
    assert generated == [InterpretationDigest] * 3
    assert [row['outcome'] for row in result['verification_audit']['lineage']] == ['support_not_established', 'accepted']
    assert not any('First attempt' in claim for claim in result['interpretation']['claims'])


@pytest.mark.parametrize('direct', [False, True])
def test_optional_number_in_map_notes_is_not_original_source_support(direct):
    mapper = ScriptedClient()
    mapper.prompt = Mock(return_value='Generated notes contain unreported 99999%.')
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [dict(CARD, rationale='Unreported 99999%')]}])
    result = _run(client, 'direct_map_reduce' if direct else 'map_reduce', mapper=mapper)
    assert result['interpretation']['status'] == 'support_not_established'
    assert result['digest']['basis'] == 'map_reduce' and len(client.calls) == 1
    assert len(mapper.calls) == 1 and mapper.prompt.call_count >= 1
    assert interpretation.selected_original(result) == TEXT
    assert all('99999' not in passage['text'] for passage in result['verification_audit']['passages'])


@pytest.mark.parametrize('direct', [False, True])
def test_optional_semantic_rejection_reuses_native_base_without_reduce_regeneration(direct):
    client = ScriptedClient(reject_optional=True)
    result = _run(client, 'direct_map_reduce' if direct else 'map_reduce')
    assert result['interpretation']['status'] == 'support_not_established'
    assert result['digest']['basis'] == 'map_reduce'
    assert sum(model is InterpretationDigest for _, model, _ in client.calls) == 1
    assert result['verification_audit']['lineage'][-1]['outcome'] == 'accepted'


@pytest.mark.parametrize('strategy', STRATEGIES)
def test_native_unavailable_fallback_clears_both_collectors_and_never_returns_base(strategy):
    class Unavailable(ScriptedClient):
        def pydantic_prompt(self, **kwargs):
            result = super().pydantic_prompt(**kwargs)
            return result if issubclass(kwargs['pydantic_model'], PaperDigest) else {}

    mapper, reducer = Unavailable(), Unavailable()
    with pytest.raises(DigestVerifierUnavailable):
        _run(reducer, strategy, mapper=mapper)
    assert sum(model is InterpretationDigest for _, model, _ in reducer.calls) == 1
    assert len([model for _, model, _ in mapper.calls if model is not None]) == 2
    assert len(reducer.calls) == 3
