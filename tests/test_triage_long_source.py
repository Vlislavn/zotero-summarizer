from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.models import RefinedSummary, SummarizeRequest, TriageResult
from zotero_summarizer.services import corpus
from zotero_summarizer.services.library import _map_reduce
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.triage import summarization


@pytest.mark.parametrize('size', [8000, 12000])
def test_long_source_reuses_configured_bounded_maps_without_losing_tail(monkeypatch, size):
    config = _default_goals_config()
    config.quality_review.map_chunk_chars = size
    llm = Mock()
    llm.prompt.return_value = 'Source evidence note.'
    monkeypatch.setattr(summarization, 'state', lambda: SimpleNamespace(
        app_state=SimpleNamespace(config=config), resolve_stage_client=lambda stage: llm))
    text = ('A source paragraph with methods and conditional results.\n' * 1700) + 'Unique final limitation.'
    monkeypatch.setattr(summarization, '_extract_pdf_text', lambda path: text)
    monkeypatch.setattr(corpus, 'run_corpus_match', lambda *args: corpus.empty_corpus_match_result())
    observed = []
    original_map = _map_reduce._map_chunk

    def capture(client, chunk, **kwargs):
        observed.append(chunk)
        return original_map(client, chunk, **kwargs)

    monkeypatch.setattr(_map_reduce, '_map_chunk', capture)
    refined = Mock(return_value=RefinedSummary(executive_summary='Source-grounded result.'))
    monkeypatch.setattr(summarization, '_refine_with_retry', refined)
    monkeypatch.setattr(summarization, '_run_triage', lambda *args: TriageResult(
        score=2, reading_priority='could_read', tags=[], rationale='Indirect fit.', dimensions={}, confidence=0.5))
    summarization.run_pipeline(SummarizeRequest(title='Long source', pdf_path='/owned.pdf'))
    assert observed == _map_reduce.split_chunks(text, size)
    assert max(map(len, observed)) <= size
    assert observed[-1].endswith('Unique final limitation.')
    assert llm.prompt.call_count == len(observed)
    assert all('untrusted_input' in call.args[0] for call in llm.prompt.call_args_list)
    context = refined.call_args.args[3]
    assert f'[chunk {len(observed)}/{len(observed)}]' in context
    assert context.count('Source evidence note.') == len(observed)


def test_short_source_keeps_direct_refinement_without_map_calls(monkeypatch):
    config = _default_goals_config()
    llm = Mock()
    monkeypatch.setattr(summarization, 'state', lambda: SimpleNamespace(
        app_state=SimpleNamespace(config=config), resolve_stage_client=lambda stage: llm))
    monkeypatch.setattr(summarization, '_extract_pdf_text', lambda path: 'Short original source.')
    monkeypatch.setattr(corpus, 'run_corpus_match', lambda *args: corpus.empty_corpus_match_result())
    refined = Mock(return_value=RefinedSummary(executive_summary='Result.'))
    monkeypatch.setattr(summarization, '_refine_with_retry', refined)
    monkeypatch.setattr(summarization, '_run_triage', lambda *args: TriageResult(
        score=2, reading_priority='could_read', tags=[], rationale='Indirect fit.', dimensions={}, confidence=0.5))
    summarization.run_pipeline(SummarizeRequest(title='Short source', pdf_path='/owned.pdf'))
    llm.prompt.assert_not_called()
    assert refined.call_args.args[3] == 'Short original source.'
