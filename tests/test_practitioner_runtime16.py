"""Synthetic data inputs, real prompt/schema/scoring IO; pinned transport/formatter, no inference."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from tools.method_validation.client import MethodValidationClient
from tools.method_validation.practitioner import (
    DiagnosticContract, GeneratorCase, load_generator_case, run_diagnostic,
)
from test_method_validation_client import real_formatting_fixture
from zotero_summarizer import runtime
from zotero_summarizer.models import RefinedSummary, SummarizeResponse, TriageResult
from zotero_summarizer.services.model import scoring
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage.prompts import DEFAULT_PRACTITIONER_TRIAGE_PROMPT, DEFAULT_TRIAGE_PROMPT

from _practitioner_inputs import practitioner_inputs as practitioner_inputs

SOURCE = Path.home() / '.pi/agent/skills/biomni-codex-subscription/scripts/pi_codex_transport.py'
SOURCE_SHA = 'ac33d26ff917d90dcf629e461342f5c9828322cae8b846da2e49320a00f286ed'
_INPUTS = None


@pytest.fixture(autouse=True)
def isolated_data_inputs(practitioner_inputs, monkeypatch):
    monkeypatch.setitem(globals(), '_INPUTS', practitioner_inputs)


def test_loaded_production_modules_bind_owned_worktree(record_property):
    import hashlib
    from tools.method_validation import client, practitioner
    from zotero_summarizer.integrations import app_rss, pdf
    owned = Path(__file__).resolve().parents[1]
    bindings = {}
    for module in (runtime, summarization, client, practitioner, app_rss, pdf):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(owned), str(path)
        bindings[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    record_property('actual_loaded_source_bindings', json.dumps(bindings))
    prefix = os.environ.get('ZS_PRACTITIONER_TEST_EVIDENCE')
    if prefix:
        with Path(prefix + '-loaded-sources.json').open('x') as handle:
            json.dump(bindings, handle, indent=2)


def contract_data(arm='production', calls=3):
    return {'case_id': 'public-03', 'rubric': arm, 'model': 'gpt-6.1-sol', 'effort': 'xhigh',
            'session_id': 'synthetic-practitioner16:' + arm, 'max_logical_calls': calls,
            'operation_timeout_seconds': 120, 'external_wall_cap_seconds': 600,
            'semantic_judge_calls': 0, 'transport_path': str(SOURCE), 'transport_sha256': SOURCE_SHA,
            'goals': {'research_goals': ['Public engineering methods'],
                      'triage_criteria': ['Grounded evidence'], 'summary_structure': ['Findings and limits'],
                      'relevance_scale': {str(i): str(i) for i in range(1, 6)},
                      'reading_priority_scale': {'could_read': 'Optional'}, 'output_language': 'English'}}


def case():
    return load_generator_case(_INPUTS.cohort, _INPUTS.cohort_sha,
                               _INPUTS.xml, _INPUTS.xml_sha, 'public-03')


@pytest.mark.parametrize('field', ['grader_html', 'questions', 'labels', 'reading_aid', 'expected_score'])
def test_generator_projection_rejects_extra_fields(field):
    values = case().model_dump(mode='json')
    values[field] = 'FORBIDDEN_GENERATOR_SENTINEL'
    with pytest.raises(ValidationError):
        GeneratorCase.model_validate(values)


@pytest.mark.parametrize('mutation', [
    {'model': 'other'}, {'effort': 'high'}, {'max_logical_calls': 64},
    {'max_logical_calls': True}, {'external_wall_cap_seconds': 2040},
    {'semantic_judge_calls': 1}, {'semantic_judge_calls': False},
    {'prompts': {'triage': 'FORBIDDEN'}},
    {'corpus': {'enabled': False}}, {'llm_routing': {}}, {'expected_score': 5},
])
def test_config_failclosed_before_any_client(mutation):
    with pytest.raises(ValidationError):
        DiagnosticContract.model_validate({**contract_data(), **mutation})


def test_one_explicit_env_contract_no_hidden_expansion(monkeypatch):
    monkeypatch.setenv('ZS_PRACTITIONER_CONTRACT', json.dumps(contract_data()))
    monkeypatch.setenv('ZS_CORPUS_ENABLED', 'false')
    monkeypatch.setenv('ZS_METHOD_OPERATION_TIMEOUT_SECONDS', '599')
    selected = DiagnosticContract.from_env()
    assert selected.operation_timeout_seconds == 120
    assert selected.product_config().corpus.enabled is True
    assert selected.product_config().prompts.triage is None
    monkeypatch.setenv('ZS_PRACTITIONER_CONTRACT', '{invalid')
    with pytest.raises(ValidationError):
        DiagnosticContract.from_env()


def test_hidden_nested_goals_config_rejected():
    values = contract_data()
    values['goals']['expected_score'] = 'FORBIDDEN'
    with pytest.raises(ValidationError):
        DiagnosticContract.model_validate(values)


def test_cohort_projection_rejects_unknown_fields_and_wrong_hash(tmp_path):
    data = json.loads(_INPUTS.cohort.read_text())
    data['cases'][0]['grader_html'] = 'FORBIDDEN'
    forged = tmp_path / 'cohort.json'
    forged.write_text(json.dumps(data))
    import hashlib
    digest = hashlib.sha256(forged.read_bytes()).hexdigest()
    with pytest.raises(ValidationError):
        load_generator_case(forged, digest, _INPUTS.xml, _INPUTS.xml_sha, 'public-03')
    with pytest.raises(ValueError, match='SHA'):
        load_generator_case(_INPUTS.cohort, '0' * 64, _INPUTS.xml, _INPUTS.xml_sha, 'public-03')


def test_synthetic_input_xml_replacement_is_rejected():
    _INPUTS.xml.write_bytes(b'<rss>replaced synthetic input</rss>')
    with pytest.raises(ValueError, match='SHA'):
        case()


@pytest.fixture
def neutral_model(monkeypatch, real_formatting_fixture):
    emitted, pending = [], []
    refined = RefinedSummary(executive_summary='Neutral synthetic summary', key_findings=['Neutral finding'],
                             methods='Neutral method', limitations='Neutral limitation')
    triage = TriageResult(score=3, rationale='Neutral synthetic rationale', confidence=0.5)
    pending.extend([refined.model_dump_json(), triage.model_dump_json()])

    def build(client):
        def invoke(prompt, **kwargs):
            emitted.append(prompt)
            value = pending.pop(0)
            if isinstance(value, Exception):
                raise value
            return AIMessage(content=value, response_metadata={'model_name': 'gpt-6.1-sol'})
        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(MethodValidationClient, '_build_model', build)
    monkeypatch.setattr(MethodValidationClient, '_auth', lambda *a: pytest.fail('credential resolution forbidden'))
    return emitted, pending, refined, triage


def test_real_emitter_paired_rubric_io_scoring_and_reopen(
    tmp_path, neutral_model, real_formatting_fixture, record_property,
):
    emitted, pending, refined, triage = neutral_model
    before = runtime._context
    selected = case()
    results = []
    for arm in ('production', 'academic'):
        if not pending:
            pending.extend([refined.model_dump_json(), triage.model_dump_json()])
        results.append(run_diagnostic(DiagnosticContract.model_validate(contract_data(arm)),
                                      selected, tmp_path / arm))
        assert runtime._context is before
    prod, academic = results
    assert prod['status'] == academic['status'] == 'diagnostic_completed'
    assert prod['logical_calls'] == academic['logical_calls'] == 2
    assert prod['actual_http_request_count'] == academic['actual_http_request_count'] == 0
    assert prod['rubric'] == 'production' and academic['rubric'] == 'academic'
    assert prod['corpus'] == {'enabled': True, 'cache_available': False}
    assert prod['semantic_judge_calls'] == 0
    config = DiagnosticContract.model_validate(contract_data()).product_config()
    request = selected.request()
    context = summarization.corpus.empty_corpus_match_result()
    assert emitted[0] == emitted[2] == summarization._build_refine_prompt(config, request, request.abstract.strip())
    production_prompt = summarization._build_triage_prompt(config, request, refined, context,
                                                          DEFAULT_PRACTITIONER_TRIAGE_PROMPT)
    academic_prompt = summarization._build_triage_prompt(config, request, refined, context, DEFAULT_TRIAGE_PROMPT)
    formatted_prod, _ = real_formatting_fixture(None, production_prompt, TriageResult)
    formatted_academic, _ = real_formatting_fixture(None, academic_prompt, TriageResult)
    assert emitted[1] == formatted_prod and emitted[3] == formatted_academic
    for arm, result in zip(('production', 'academic'), results, strict=True):
        folder = tmp_path / arm
        events = json.loads((folder / 'events.json').read_text())
        response = SummarizeResponse.model_validate_json((folder / 'response.json').read_text())
        assert json.loads((folder / 'receipt.json').read_text()) == result
        assert response.composite_relevance_score == scoring.compute_composite_score(triage, 0)
        assert response.reading_priority == scoring.map_priority_from_score(response.composite_relevance_score)
        assert response.methods == refined.methods and response.key_findings == refined.key_findings
        assert all(v is None for v in events[-1]['usage'].values())
        assert all(e['requests'] == [] for e in events)
        assert selected.abstract in emitted[0]
        assert 'FORBIDDEN_GENERATOR_SENTINEL' not in ''.join(emitted)
        assert 'rss-content.html' not in ''.join(emitted)
        assert result['input']['stable_feed_key'] == selected.stable_feed_key
        assert result['processed_feed_persistence'] is False
    record_property('neutral_emitter_receipts', json.dumps(results))
    prefix = os.environ.get('ZS_PRACTITIONER_TEST_EVIDENCE')
    if prefix:
        with Path(prefix + '-caller-io.json').open('x') as handle:
            json.dump({'receipts': results, 'emitted_prompts': emitted}, handle, indent=2)


def test_existing_single_refinement_repair_is_charged(tmp_path, neutral_model):
    emitted, pending, refined, triage = neutral_model
    pending[:] = ['not JSON', refined.model_dump_json(), triage.model_dump_json()]
    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data()), case(), tmp_path / 'repair')
    assert receipt['logical_calls'] == 3 and len(emitted) == 3
    assert receipt['status'] == 'diagnostic_completed'
    assert json.dumps(RefinedSummary.model_json_schema(), ensure_ascii=False) in emitted[1]


def test_no_budget_reset_hidden_retry_or_raw_exception_capture(tmp_path, neutral_model):
    emitted, pending, _, _ = neutral_model
    pending[:] = [RuntimeError('PRIVATE_EXCEPTION_SENTINEL')]
    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data(calls=1)), case(), tmp_path / 'failure')
    assert receipt['status'] == 'transport_failure'
    assert receipt['logical_calls'] == len(emitted) == 1
    assert 'PRIVATE_EXCEPTION_SENTINEL' not in json.dumps(receipt)
    assert 'PRIVATE_EXCEPTION_SENTINEL' not in (tmp_path / 'failure/events.json').read_text()


def test_exhaustion_preserves_failure_receipts(tmp_path, neutral_model):
    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data(calls=1)), case(), tmp_path / 'budget')
    assert receipt['status'] == 'pipeline_failure'
    assert receipt['logical_calls'] == 1
    assert (tmp_path / 'budget/events.json').is_file()
    assert not (tmp_path / 'budget/response.json').exists()


def test_existing_output_refused_before_client(tmp_path, neutral_model):
    root = tmp_path / 'occupied'
    root.mkdir()
    (root / '.env').write_text('DO NOT READ')
    with pytest.raises(FileExistsError):
        run_diagnostic(DiagnosticContract.model_validate(contract_data()), case(), root)
    assert neutral_model[0] == []


def test_input_mismatch_failclosed_before_outputs(tmp_path, neutral_model):
    mismatched = copy.deepcopy(contract_data())
    mismatched['case_id'] = 'public-08'
    with pytest.raises(ValueError, match='case_id'):
        run_diagnostic(DiagnosticContract.model_validate(mismatched), case(), tmp_path / 'no-output')
    assert not (tmp_path / 'no-output').exists()


@pytest.mark.parametrize('arm', ['production', 'academic'])
def test_missing_abstract_nonattempt_is_not_abstention(tmp_path, neutral_model, arm):
    missing = case().model_copy(update={'abstract': ''})
    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data(arm)), missing,
                             tmp_path / arm)
    assert receipt['status'] == 'nonattempt_missing_abstract'
    assert receipt['logical_calls'] == 0
    assert neutral_model[0] == []


def test_existing_corpus_fast_reject_remains_censorship_not_success(tmp_path, neutral_model, monkeypatch):
    context = {**summarization.corpus.empty_corpus_match_result(),
               'has_corpus': True, 'affinity_score': -0.9}
    monkeypatch.setattr(summarization.corpus, 'run_corpus_match', lambda *a: context)
    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data()), case(), tmp_path / 'gate')
    assert receipt['status'] == 'gate_censored'
    assert receipt['logical_calls'] == 0
    assert neutral_model[0] == []
    summary = SummarizeResponse.model_validate_json((tmp_path / 'gate/response.json').read_text())
    assert summary.tags == ['prefilter_low_corpus_affinity']


def test_context_reentrancy_refused_without_threads_and_config_is_detached(tmp_path, neutral_model):
    frozen = DiagnosticContract.model_validate(contract_data())
    selected = case()
    before = runtime._context
    callbacks = []

    def observe(event):
        if event['event'] == 'call_started':
            frozen.goals.relevance_scale[1] = 'FORBIDDEN_GENERATOR_SENTINEL'
            with pytest.raises(RuntimeError, match='concurrent'):
                run_diagnostic(frozen, selected, tmp_path / 'reentrant')
            callbacks.append(event['logical_call'])

    receipt = run_diagnostic(frozen, selected, tmp_path / 'outer', observer=observe)
    assert receipt['status'] == 'diagnostic_completed'
    assert callbacks == [1, 2]
    assert runtime._context is before
    assert not (tmp_path / 'reentrant').exists()
    assert 'FORBIDDEN_GENERATOR_SENTINEL' not in ''.join(neutral_model[0])


def test_transport_source_mismatch_is_nonattempt_not_transport_failure(tmp_path, neutral_model):
    invalid = DiagnosticContract.model_validate({**contract_data(), 'transport_sha256': '0' * 64})
    receipt = run_diagnostic(invalid, case(), tmp_path / 'refused-source')
    assert receipt['status'] == 'nonattempt_setup_failure'
    assert receipt['logical_calls'] == 0
    assert neutral_model[0] == []


@pytest.mark.parametrize('arm', ['production', 'academic'])
@pytest.mark.parametrize('tag', [
    'prefilter_low_corpus_affinity', 'abstract_missing', 'ordinary-generated-tag',
])
def test_generated_tags_cannot_override_successful_call_evidence(
    tmp_path, neutral_model, arm, tag, record_property,
):
    emitted, pending, refined, triage = neutral_model
    tagged = triage.model_copy(update={'tags': [tag]})
    pending[:] = [refined.model_dump_json(), tagged.model_dump_json()]
    folder = tmp_path / arm

    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data(arm)), case(), folder)
    events = json.loads((folder / 'events.json').read_text())
    response = SummarizeResponse.model_validate_json((folder / 'response.json').read_text())

    assert receipt['logical_calls'] == len(emitted) == 2
    assert receipt['actual_http_request_count'] == 0
    assert [e['logical_call'] for e in events if e['event'] == 'call_started'] == [1, 2]
    assert [e['status'] for e in events if e['event'] == 'call_terminal'] == ['success', 'success']
    assert response.tags == [tag]
    assert json.loads((folder / 'receipt.json').read_text()) == receipt
    evidence = {'arm': arm, 'tag': tag, 'receipt': receipt, 'events': events,
                'response': response.model_dump(mode='json')}
    record_property('generated_marker_call_evidence', json.dumps(evidence))
    prefix = os.environ.get('ZS_PRACTITIONER_TEST_EVIDENCE')
    if prefix:
        with Path(prefix + f'-marker-{arm}-{tag}.json').open('x') as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2)
    assert receipt['status'] == 'diagnostic_completed'


@pytest.mark.parametrize(('events', 'expected'), [
    ([], 'nonattempt_setup_failure'),
    ([{'event': 'call_started'}], 'nonattempt_setup_failure'),
    ([{'event': 'call_started'}, {'event': 'call_terminal', 'status': 'transport_failure'}],
     'transport_failure'),
    ([{'event': 'call_started'}, {'event': 'call_terminal', 'status': 'parse_failure'}],
     'parse_failure'),
    ([{'event': 'call_started'}, {'event': 'call_terminal', 'status': 'success'}],
     'pipeline_failure'),
])
def test_null_summary_accounting_preserves_trusted_failure_branch(events, expected):
    from tools.method_validation.practitioner import _outcome

    assert _outcome(None, events) == expected


@pytest.mark.parametrize(('field', 'replacement'), [
    ('executive_summary', 'Changed persisted public summary'),
    ('tags', ['changed-persisted-tag']),
    ('relevance_score', 5),
])
def test_public_response_tamper_is_rejected_on_reopen(
    tmp_path, neutral_model, monkeypatch, field, replacement, record_property,
):
    from tools.method_validation import practitioner

    context = {**summarization.corpus.empty_corpus_match_result(),
               'has_corpus': True, 'affinity_score': -0.9}
    monkeypatch.setattr(summarization.corpus, 'run_corpus_match', lambda *a: context)
    original_save = practitioner._save
    captured = []

    def tamper_response(path, value):
        if path.name == 'response.json':
            captured.append(copy.deepcopy(value))
            value = {**value, field: replacement}
        original_save(path, value)

    monkeypatch.setattr(practitioner, '_save', tamper_response)
    before = runtime._context
    folder = tmp_path / 'tampered-public'
    receipt = run_diagnostic(DiagnosticContract.model_validate(contract_data()), case(), folder)
    response = SummarizeResponse.model_validate_json((folder / 'response.json').read_text())
    public = response.model_dump(mode='json')
    events = json.loads((folder / 'events.json').read_text())
    evidence = {'field': field, 'replacement': replacement, 'original_public': captured,
                'persisted_public': public, 'receipt': receipt, 'events': events}
    record_property('public_roundtrip_tamper', json.dumps(evidence))
    prefix = os.environ.get('ZS_PRACTITIONER_TEST_EVIDENCE')
    if prefix:
        with Path(prefix + f'-roundtrip-tamper-{field}.json').open('x') as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2)
    assert len(captured) == 1
    assert public == {**captured[0], field: replacement}
    assert public != captured[0]
    assert response._corpus_fast_rejected is False
    assert receipt['status'] == 'nonattempt_setup_failure'
    assert receipt['error_class'] == 'ValueError'
    assert receipt['logical_calls'] == receipt['actual_http_request_count'] == 0
    assert neutral_model[0] == events == []
    assert json.loads((folder / 'receipt.json').read_text()) == receipt
    assert runtime._context is before
