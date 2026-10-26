"""Current explicit QA plus ordinary history/mode controls; no old allocator adapter."""
import builtins
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, inputs, run_review
from tests.test_interpretation_experiment_consumers import ask, Reply
from tests.test_library_qa import _Extractor, _LLM, _fake_state
from tests.test_qa_history_prompt_boundary import _expected_prompt, _history_request
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _interpretation_qa as iq, _paper_read_meta as meta, _review_cache, qa, qa_context
from zotero_summarizer.services.library._interpretation_trace import Trace
from zotero_summarizer.services.library._prompt_security import untrusted_input

KEY = 'LIBR0038'
QUESTION = 'Which dataset was used?'
QUOTE = 'We evaluated GlassNet on the ImageNet dataset.'
ORIGINAL_QUOTE = 'The sample retained alpha. The studied cohort was adults. The design was observational.'
ORDINARY_ARTIFACT_TEXT = qa.paper_render.artifact_text


def _ordinary(tmp_path, monkeypatch, *, body=QUOTE):
    client = _LLM()
    client.prompt = Mock(return_value=json.dumps({'answer': 'ImageNet', 'quote': QUOTE}))
    app = _fake_state(tmp_path, tmp_path / 'unused.pdf', _Extractor(), client, monkeypatch)
    artifact = {'item_key': KEY, 'pdf_key': 'owned-extraction', 'title': 'Owned', 'full_text': body}
    monkeypatch.setattr(qa.paper_render, 'build_paper_read', Mock(return_value=artifact))
    monkeypatch.setattr(qa.paper_render, 'artifact_text', ORDINARY_ARTIFACT_TEXT)
    return app, client, artifact


@pytest.mark.parametrize('delta', [0, 1, -1])
def test_whole_bundle_exact_boundary_and_input_isolation(delta):
    result = run_review(ScriptedClient())
    before = deepcopy(result)
    legacy = meta.artifact_text(result['content'], max_chars=20000, review={'digest': result['digest']})
    extra = '\n\nAI interpretation draft (not original evidence):\n' + json.dumps(result['interpretation']['items'], ensure_ascii=False)
    client = ScriptedClient()
    output = ask(result, client, cap=len(legacy) + len(extra) + delta)
    expected = legacy + extra if delta >= 0 else legacy
    assert output['card_visibility'] == ('whole_card' if delta >= 0 else 'whole_card_over_budget')
    assert client.calls[-1][0] == iq.ANSWER_PROMPT.format(context=untrusted_input(expected),
        question=untrusted_input('What did the paper establish?')) + iq.CAUTION
    assert result == before


def test_structural_origin_with_repeated_body_in_title_and_digest():
    paper, _, _ = inputs()
    paper['content']['title'] = TEXT
    result = run_review(ScriptedClient(), paper=paper)
    client = ScriptedClient()
    cap = 100
    output = ask(result, client, cap=cap)
    expected = meta.artifact_text(result['content'], max_chars=cap, review={'digest': result['digest']})
    assert output['card_visibility'] == 'whole_card_over_budget'
    assert untrusted_input(expected) in client.calls[-1][0]
    assert expected == ('Title: ' + TEXT)[:cap]


@pytest.mark.parametrize('body_fields,body', [({'full_text': 'fallback'}, 'fallback'),
    ({'qa_text': None, 'full_text': 'WRONG'}, ''), ({'qa_text': '', 'full_text': 'WRONG'}, ''),
    ({'qa_text': ' \t\n', 'full_text': 'WRONG'}, ' \t\n')])
def test_raw_precedence_empty_and_whitespace(body_fields, body):
    artifact = {'title': 'Owned', **body_fields}
    assert meta.qa_body_text(artifact) == body
    header = 'Title: Owned\n\nPages: 0\n\nFigures: 0\n\nReferences: 0'
    assert meta.artifact_text(artifact, max_chars=60000) == header + ('\n\n' + body if body else '')


@pytest.mark.parametrize('cap', [0, -1, -100000, 1, 44, 60, 800])
def test_saturated_and_nonpositive_caps_preserve_python_slice(cap):
    result = run_review(ScriptedClient())
    full = meta.artifact_text(result['content'], max_chars=100000, review={'digest': result['digest']})
    assert meta.artifact_text(result['content'], max_chars=cap, review={'digest': result['digest']}) == full[:cap]
    client = ScriptedClient()
    if cap <= 0:
        with pytest.raises(ValueError, match='context budget'):
            ask(result, client, cap=cap)
        assert client.calls == []
    else:
        assert ask(result, client, cap=cap)['card_visibility'] == 'whole_card_over_budget'
        assert untrusted_input(full[:cap]) in client.calls[-1][0]


def test_native_connected_snapshot_once_exact_view_and_readonly_io(tmp_path, monkeypatch):
    result = run_review(ScriptedClient())
    path = tmp_path / 'record.json'
    path.write_text(json.dumps(result))
    before = path.read_bytes(), path.stat().st_mtime_ns
    frozen = json.loads(path.read_text())
    forbidden = Mock(side_effect=AssertionError('explicit QA must not consult or write ordinary cache'))
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    monkeypatch.setattr(_review_cache, '_write_one', forbidden)
    client = Reply(PAYLOAD['tldr'], ORIGINAL_QUOTE)
    output = ask(frozen, client)
    assert output['grounding'] == 'accepted' and output['response']['answer'] == PAYLOAD['tldr']
    assert output['card_visibility'] == 'whole_card' and len(client.calls) == 1
    assert json.dumps([CARD], ensure_ascii=False) in client.calls[-1][0]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize('case', ['missing_optional', 'tampered_optional', 'absent', 'stale', 'source_missing'])
def test_connected_native_unavailable_uses_same_qualified_base_or_none(case):
    result = run_review(ScriptedClient(cards=case != 'missing_optional'))
    client = Reply(PAYLOAD['tldr'], ORIGINAL_QUOTE)
    if case == 'tampered_optional':
        result['interpretation']['items'][0]['rationale'] = 'Never revive this optional body'
    elif case == 'absent':
        result = {}
    elif case == 'stale':
        result['digest']['tldr'] = 'Different base'
    elif case == 'source_missing':
        result['text'] = ''
    if case in {'absent', 'stale', 'source_missing'}:
        with pytest.raises((ValueError, KeyError)):
            ask(result, client)
        assert client.calls == []
    else:
        output = ask(result, client)
        assert output['grounding'] == 'accepted' and output['response']['answer'] == PAYLOAD['tldr']
        assert output['card_visibility'] in {'unavailable', 'proof_unavailable'}
        assert 'Never revive' not in client.calls[-1][0]


@pytest.mark.parametrize('case', ['corrupt', 'getter_error', 'malformed', 'unknown'])
def test_used_c_errors_do_not_forge_success_or_call_model(tmp_path, case):
    result = run_review(ScriptedClient())
    client = ScriptedClient()
    if case == 'corrupt':
        path = tmp_path / 'record.json'
        path.write_bytes(b'{')
        with pytest.raises(ValueError):
            json.loads(path.read_text())
        assert path.read_bytes() == b'{'
    else:
        if case == 'getter_error':
            result['text'] = 'Changed extraction'
        elif case == 'malformed':
            del result['verification_audit']
        with pytest.raises((ValueError, KeyError)):
            ask(result, client, condition='unknown' if case == 'unknown' else 'C')
    assert client.calls == []


@pytest.mark.parametrize('stored_c', [False, True])
def test_off_exact_calls_shape_no_optional_work(tmp_path, monkeypatch, stored_c):
    if stored_c:
        _review_cache._write_one(KEY, run_review(ScriptedClient()))
    app, client, artifact = _ordinary(tmp_path, monkeypatch)
    original_import = builtins.__import__
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        assert 'interpretation' not in name and not any('interpretation' in value for value in (fromlist or ()))
        return original_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(qa, 'perf_counter', lambda: 1.0)
    result = qa.ask_paper(KEY, QUESTION)
    expected = meta.artifact_text(artifact, max_chars=60000)
    client.prompt.assert_called_once_with(qa.ANSWER_PROMPT.format(context=untrusted_input(expected), question=untrusted_input(QUESTION)))
    handle = qa_context.evidence_handle(KEY, artifact, QUESTION, QUOTE)
    assert result == {'item_key': KEY, 'question': QUESTION, 'answer': 'ImageNet', 'abstained': False,
        'quote': QUOTE, 'mode': 'comprehensive', 'chunks_used': 0, 'latency_seconds': 0.0, 'model': 'local-35b',
        'history_compacted': False, 'history_evidence_handles': 0, 'evidence_handle': handle,
        'citation': {'claimed': True, 'quote_verified': True, 'location_verified': True, 'evidence_handle': handle}}


@pytest.mark.parametrize('mode,question', [('retrieval', QUESTION), ('full_text', QUESTION),
                                         ('comprehensive', 'How many pages are in the paper?')])
def test_unused_private_keyword_does_no_c_work(tmp_path, monkeypatch, mode, question):
    _, client, _ = _ordinary(tmp_path, monkeypatch)
    forbidden = Mock(side_effect=AssertionError('ordinary mode must not consult optional records'))
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    result = qa.ask_paper(KEY, question, mode=mode)
    assert 'interpretation_context' not in result
    assert client.prompt.call_count == (0 if mode == 'comprehensive' else 1)
    forbidden.assert_not_called()


@pytest.mark.parametrize('mode', ['retrieval', 'full_text'])
def test_other_mode_empty_extraction_still_raises(tmp_path, monkeypatch, mode):
    _, client, _ = _ordinary(tmp_path, monkeypatch, body=' \t\n')
    with pytest.raises(APIError) as caught:
        qa.ask_paper(KEY, QUESTION, mode=mode)
    assert caught.value.error == 'extraction_empty' and caught.value.status_code == 422
    client.prompt.assert_not_called()


@pytest.mark.parametrize('answer', [PAYLOAD['tldr'], 'association'])
@pytest.mark.parametrize('delta', [0, 1, -1])
def test_connected_boundary_authority_firewall(answer, delta):
    result = run_review(ScriptedClient())
    legacy = meta.artifact_text(result['content'], max_chars=20000, review={'digest': result['digest']})
    extra = '\n\nAI interpretation draft (not original evidence):\n' + json.dumps([CARD], ensure_ascii=False)
    output = ask(result, Reply(answer, ORIGINAL_QUOTE), cap=len(legacy) + len(extra) + delta)
    assert output['card_visibility'] == ('whole_card_over_budget' if delta < 0 else 'whole_card')
    assert output['response']['answer'] == (PAYLOAD['tldr'] if answer == PAYLOAD['tldr'] else None)


@pytest.mark.parametrize('in_original', [False, True])
def test_connected_overflow_retains_legacy_late_quote_policy(in_original):
    # Legacy digest context may cite a late quote only if it also belongs to the actual original.
    late = 'A unique late sentence describes a secret benchmark with perfect accuracy.'
    paper, _, _ = inputs()
    if in_original:
        paper['text'] += late
        paper['content']['full_text'] = paper['text']
    payload = PAYLOAD | {'key_findings': [late], 'interpretation': None}
    result = run_review(ScriptedClient(cards=False, responses=[payload]), paper=paper)
    full = meta.artifact_text(result['content'], max_chars=100000, review={'digest': result['digest']})
    cap = len(full) - len(paper['text']) + len(ORIGINAL_QUOTE)
    legacy = meta.artifact_text(result['content'], max_chars=cap, review={'digest': result['digest']})
    assert late in legacy and late not in paper['text'][:len(ORIGINAL_QUOTE)]
    output = ask(result, Reply('secret benchmark', late), cap=cap)
    assert output['response']['abstained'] is (not in_original)
    assert output['response']['answer'] == ('secret benchmark' if in_original else None)
    assert output['grounding'] == ('accepted' if in_original else 'legacy_authority_rejected')


@pytest.mark.parametrize('answer,quote,accepted', [(PAYLOAD['tldr'], ORIGINAL_QUOTE, True),
    ('association', ORIGINAL_QUOTE, False), (PAYLOAD['tldr'], 'Invented source quote with enough words to look plausible.', False)])
def test_injected_adapter_orthogonal_guard_composition(answer, quote, accepted):
    # Real allocator and grounding owners, not a fabricated adapter success.
    result = run_review(ScriptedClient())
    output = ask(result, Reply(answer, quote))
    assert output['response']['abstained'] is (not accepted)
    assert output['grounding'] == ('accepted' if accepted else 'legacy_authority_rejected')


@pytest.mark.parametrize('responses,abstained', [((json.dumps({'answer': 'ImageNet', 'quote': QUOTE}),), False),
    (('{"answer":null,"quote":null}',), True), (('not JSON', json.dumps({'answer': 'ImageNet', 'quote': QUOTE})), False),
    (('not JSON', 'still not JSON'), None)])
@pytest.mark.parametrize('history_kind', [0, 4, 'compacted'])
def test_connected_history_escaping_and_real_attempt_budget(tmp_path, monkeypatch, responses, abstained, history_kind):
    from tests.test_library_qa import PAPER_TEXT
    from tests.test_qa_history_prompt_boundary import _UNSAFE
    _, client, artifact = _ordinary(tmp_path, monkeypatch, body=PAPER_TEXT + '\n' + _UNSAFE)
    request, history = _history_request(artifact, history_kind)
    prior, compacted, handles = qa_context.compact_history(request.item_key, artifact, history)
    expected = _expected_prompt(meta.artifact_text(artifact, max_chars=60000), prior)
    client.prompt.side_effect = responses
    if abstained is None:
        with pytest.raises(ValueError):
            qa.ask_paper(request.item_key, request.question, history=history)
    else:
        output = qa.ask_paper(request.item_key, request.question, history=history)
        assert output['abstained'] is abstained
        assert (output['history_compacted'], output['history_evidence_handles']) == (compacted, handles)
    prompts = [call.args[0] for call in client.prompt.call_args_list]
    assert prompts == [expected] + (['Extract ONE valid JSON object {"answer": ..., "quote": ...} from the '
                                   'following text and return ONLY it:\n\n' + responses[0]] if len(responses) == 2 else [])


@pytest.mark.parametrize('selection', ['A', 'C'])
def test_guard_primitive_arguments_and_short_circuit_order(monkeypatch, selection):
    result = run_review(ScriptedClient())
    legacy = meta.artifact_text(result['content'], max_chars=20000, review={'digest': result['digest']})
    quotes = Mock(return_value=True)
    answers = Mock(side_effect=[False, True, True])
    monkeypatch.setattr(iq, 'quote_is_grounded', quotes)
    monkeypatch.setattr(iq, 'answer_is_supported_by_quote', answers)
    trace = Mock()
    trace.attach_mock(quotes, 'quote')
    trace.attach_mock(answers, 'answer')
    output = ask(result, Reply(PAYLOAD['tldr'], ORIGINAL_QUOTE), condition=selection)
    supplied = legacy + ('\n\nAI interpretation draft (not original evidence):\n' + json.dumps([CARD], ensure_ascii=False) if selection == 'C' else '')
    assert [call.args for call in quotes.call_args_list] == [(ORIGINAL_QUOTE, legacy), (ORIGINAL_QUOTE, result['text']), (ORIGINAL_QUOTE, supplied)]
    assert [call.args for call in answers.call_args_list] == [(PAYLOAD['tldr'], ORIGINAL_QUOTE), (PAYLOAD['tldr'], legacy), (PAYLOAD['tldr'], supplied)]
    assert [call[0] for call in trace.mock_calls] == ['quote', 'quote', 'quote', 'answer', 'answer', 'answer']
    assert output['response']['answer'] == PAYLOAD['tldr']
    quotes.reset_mock(return_value=True)
    answers.reset_mock()
    quotes.return_value = False
    refused = ask(result, Reply(PAYLOAD['tldr'], ORIGINAL_QUOTE), condition=selection)
    assert refused['response']['abstained'] is True and quotes.call_count == 1
    answers.assert_not_called()


def test_legacy_default_str_and_serialization_before_body_are_preserved(monkeypatch):
    marker = Mock()
    marker.__str__ = Mock(return_value='π')
    body = Mock(return_value='raw')
    monkeypatch.setattr(meta, 'qa_body_text', body)
    trace = Mock()
    trace.attach_mock(marker.__str__, 'serialize')
    trace.attach_mock(body, 'body')
    actual = meta.artifact_text({'title': 'Owned'}, max_chars=60000, review={'digest': {'marker': marker}})
    assert actual == 'Title: Owned\n\nPages: 0\n\nFigures: 0\n\nReferences: 0\n\nStructured review:\n{"digest": {"marker": "π"}}\n\nraw'
    assert [call[0] for call in trace.mock_calls] == ['serialize', 'body']


def test_connected_c_adds_no_body_admission_calls(monkeypatch):
    from zotero_summarizer.services.library import _source_admission
    result = run_review(ScriptedClient())
    admission = Mock(wraps=_source_admission.admit_source)
    monkeypatch.setattr(_source_admission, 'admit_source', admission)
    client = ScriptedClient()
    ask(result, client)
    assert [call.args for call in admission.call_args_list] == [(TEXT,), (TEXT,)]
    assert len(client.calls) == 1  # no extraction or additional generation/verification
