"""Ordinary computation guarantees plus explicit experimental producer counterparts."""
import builtins
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests._generated_write_fixtures import config, paper_io, published_review
from tests._interpretation_experiment import CARD, PAYLOAD, ScriptedClient, run_review
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _digest_audit, _map_reduce, _review_cache, _review_identity, deep_review
from zotero_summarizer.services.library._digest_verification import DigestSourceRejected, DigestVerifierUnavailable
from zotero_summarizer.services.zotero import zotero


def _compute(config, io, client, **kwargs):
    return deep_review._review_one({'item_key': 'LIBR0038', 'pdf_path': str(io.source)}, reader=io.reader,
        config=config, extractor=io.extractor, llm=client, quality_enabled=True, **kwargs)


@pytest.mark.parametrize('strategy', ['rank', 'prefix', 'map_reduce'])
def test_private_c_native_entry_binds_same_collector_source_and_readonly_cache(strategy):
    # Current explicit experiment producer, not an ordinary-C worker or cache adapter.
    client = ScriptedClient()
    result = run_review(client, strategy=strategy)
    from zotero_summarizer.services.library.interpretation import qualify, selected_original
    assert qualify(result)['items'] == [CARD]
    assert result['verification_audit']['source']['original_characters'] == len(result['text'])
    assert selected_original(result)
    assert 'interpretation' not in result['digest']
    assert not _review_cache._cache_path().exists()


@pytest.mark.parametrize('strategy', ['rank', 'prefix', 'map_reduce'])
@pytest.mark.parametrize('lean_tier', [False, True])
def test_default_exact_keyword_key_shape_no_c_import_and_original_native_mirror(config, paper_io, monkeypatch, strategy, lean_tier):
    config.quality_review.chunk_strategy = strategy
    client = ScriptedClient(cards=False)
    relay = Mock(wraps=_map_reduce.digest_for_strategy)
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    forbidden = Mock(side_effect=AssertionError('computation must not acquire a native writer'))
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert 'interpretation' not in name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(_map_reduce, 'digest_for_strategy', relay)
    monkeypatch.setattr(_digest_audit, 'bind_cached_audit', binder)
    monkeypatch.setattr(zotero, 'get_zotero_writer_or_raise', forbidden)
    entry = _compute(config, paper_io, client, lean_tier=lean_tier)
    expected_cap = config.quality_review.lean_max_text_chars if lean_tier else config.quality_review.max_text_chars
    assert relay.call_args.kwargs['budget'].max_chars == expected_cap
    assert set(relay.call_args.kwargs) == {'map_llm', 'reduce_llm', 'budget', 'focus_prompt', 'response_format', 'verification_audit'}
    assert relay.call_args.kwargs['map_llm'] is client and relay.call_args.kwargs['reduce_llm'] is client
    assert relay.call_count == binder.call_count == 1 and binder.call_args.kwargs == {}
    assert set(entry) == {'review_contract_version', 'review_identity', 'provenance', 'digest', 'verification_audit',
        'quality', 'goal_summaries', 'paper_type', 'section_overlay', 'code_link', 'model_digest', 'model_read_decision',
        'reading_policy_flags', 'needs_pdf', 'needs_login', 'login_url', 'gate_relevance', 'reviewed_at',
        'zotero_note_written', 'zotero_note_error'}
    assert 'interpretation_schema' not in entry['review_identity']
    assert entry['review_identity']['source_kind'] == 'override'
    assert entry['verification_audit']['identity']['review_source_sha256'] == entry['review_identity']['source_sha256']
    assert entry['zotero_note_written'] is False and entry['zotero_note_error'] is None
    assert all(model is PaperDigest for _, model, _ in client.calls if model is not None and issubclass(model, PaperDigest))
    paper_io.note_stage.assert_not_called()
    forbidden.assert_not_called()
    assert not _review_cache._cache_path().exists()


@pytest.mark.parametrize('selection', ['unknown', '', 'paper-digest-v1', False, 0])
def test_unknown_condition_refuses_before_item_reader_model_identity_or_native_io(monkeypatch, selection):
    client = ScriptedClient()
    forbidden = Mock(side_effect=AssertionError('invalid explicit condition must refuse before work'))
    monkeypatch.setattr(_review_identity, 'build_review_identity', forbidden)
    monkeypatch.setattr(_map_reduce, 'digest_for_strategy', forbidden)
    with pytest.raises(ValueError, match='Unknown interpretation condition'):
        run_review(client, selection)
    assert client.calls == []
    forbidden.assert_not_called()


@pytest.mark.parametrize('case', ['missing_item', 'missing_pdf', 'disabled', 'empty_extraction'])
def test_c_without_a_generated_digest_never_forges_empty_optional_content(config, paper_io, case):
    client = ScriptedClient(cards=False)
    item = {'item_key': 'LIBR0038'}
    if case == 'missing_item':
        paper_io.reader.get_item_detail.return_value = None
    elif case == 'missing_pdf':
        paper_io.reader.get_item_detail.return_value['pdf_path'] = ''
    else:
        item['pdf_path'] = str(paper_io.source)
    if case == 'empty_extraction':
        paper_io.extractor.extract_text.return_value = ' \n\t'
        with pytest.raises(ValueError, match='PDF extraction returned no text'):
            deep_review._review_one(item, reader=paper_io.reader, config=config, extractor=paper_io.extractor,
                                    llm=client, quality_enabled=True)
    else:
        entry = deep_review._review_one(item, reader=paper_io.reader, config=config, extractor=paper_io.extractor,
                                       llm=client, quality_enabled=case != 'disabled')
        if case == 'missing_item':
            assert entry is None
        else:
            assert entry['digest'] is None and entry['verification_audit'] is None
            assert 'interpretation' not in entry and entry['zotero_note_written'] is False
            assert entry['needs_pdf'] is (case == 'missing_pdf')
    assert client.calls == []
    paper_io.layers.assert_not_called()
    paper_io.note_stage.assert_not_called()


@pytest.mark.parametrize('strategy', ['rank', 'prefix', 'map_reduce'])
@pytest.mark.parametrize('optional', ['missing', 'malformed', 'numeric', 'semantic'])
def test_native_optional_unavailable_is_bound_truthfully_and_only_valid_base_survives_cache_read(strategy, optional):
    card = None if optional == 'missing' else [] if optional == 'malformed' else [dict(CARD, rationale='99999%')] if optional == 'numeric' else [CARD]
    client = ScriptedClient(cards=False, reject_optional=optional == 'semantic', responses=[PAYLOAD | {'interpretation': card}])
    result = run_review(client, strategy=strategy)
    assert result['interpretation']['status'] == {'missing': 'unavailable', 'malformed': 'invalid',
        'numeric': 'support_not_established', 'semantic': 'support_not_established'}[optional]
    assert result['verification_audit']['lineage'][-1]['outcome'] == 'accepted'
    assert sum(model is not None and issubclass(model, PaperDigest) for _, model, _ in client.calls) == 1
    _review_cache._write_one('LIBR0038', result)
    assert _review_cache.get_current_review('LIBR0038') is None  # not an ordinary cache artifact


@pytest.mark.parametrize('stage', ['extract', 'generation', 'layers', 'configuration'])
def test_known_source_mutation_refuses_before_native_mirror_or_final_binding(config, paper_io, monkeypatch, stage):
    client = ScriptedClient(cards=False)
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    monkeypatch.setattr(_digest_audit, 'bind_cached_audit', binder)

    def change():
        if stage == 'configuration':
            config.research_goals.append('Changed generation goal')
        else:
            paper_io.source.write_bytes(b'R' * paper_io.source.stat().st_size)

    if stage == 'extract':
        def extraction(path):
            change()
            return paper_io.text
        paper_io.extractor.extract_text.side_effect = extraction
    elif stage == 'generation':
        original = client.pydantic_prompt
        def generation(**kwargs):
            value = original(**kwargs)
            if issubclass(kwargs['pydantic_model'], PaperDigest):
                change()
            return value
        client.pydantic_prompt = generation
    else:
        def layers(ctx):
            change()
            return (None,) * 5
        paper_io.layers.side_effect = layers
    with pytest.raises(ValueError, match='source changed'):
        _compute(config, paper_io, client)
    binder.assert_not_called()
    paper_io.note_stage.assert_not_called()
    assert not _review_cache._cache_path().exists()


@pytest.mark.parametrize('failure', ['base_rejected', 'verifier_unavailable', 'generator_exception'])
def test_native_failure_returns_no_forged_entry_and_does_not_bind_or_write(config, paper_io, monkeypatch, failure):
    client = ScriptedClient(cards=False)
    original = client.pydantic_prompt
    def fail(**kwargs):
        if failure == 'generator_exception':
            raise RuntimeError('scripted generator failure')
        result = original(**kwargs)
        if not issubclass(kwargs['pydantic_model'], PaperDigest):
            if failure == 'verifier_unavailable':
                return {}
            result['checks'][0] = dict(result['checks'][0], supported=False, evidence=[])
        return result
    client.pydantic_prompt = fail
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    monkeypatch.setattr(_digest_audit, 'bind_cached_audit', binder)
    error = RuntimeError if failure == 'generator_exception' else DigestVerifierUnavailable if failure == 'verifier_unavailable' else DigestSourceRejected
    with pytest.raises(error):
        _compute(config, paper_io, client)
    binder.assert_not_called()
    paper_io.note_stage.assert_not_called()
    assert not _review_cache._cache_path().exists()


def test_each_paper_gets_a_distinct_collector_and_missing_next_card_never_reuses_first():
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [CARD]}, PAYLOAD, PAYLOAD | {'tldr': '99999%'}])
    first = run_review(client)
    saved = deepcopy(first)
    second = run_review(client)
    assert first['interpretation']['items'] == [CARD] and second['interpretation'] == {'status': 'unavailable'}
    client.responses.append(PAYLOAD | {'tldr': '99999%'})
    with pytest.raises(DigestSourceRejected):
        run_review(client)
    assert first == saved


def test_worker_publishes_before_proposal_staging_without_native_write(published_review):
    env = published_review
    assert env.job['status'] == 'ready' and env.job['error'] is None
    assert len(env.stage_snapshots) == 1
    entry, revision = env.stage_snapshots[0]
    assert revision and entry == env.entry
    assert entry['zotero_note_written'] is False
    assert entry['verification_audit']['identity']['review_source_sha256'] == entry['review_identity']['source_sha256']
    env.writer.apply_changes.assert_not_called()
