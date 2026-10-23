import json
from copy import deepcopy

import pytest

from tests._interpretation_experiment import CARD, ScriptedClient, inputs
from tests.test_interpretation_experiment_core import run


def ask(result, client, *, condition='C', cap=20000):
    from zotero_summarizer.services.library._interpretation_qa import answer
    from zotero_summarizer.services.library._interpretation_trace import Trace
    return answer(result, 'What did the paper establish?', client,
                  max_chars=cap, condition=condition, trace=Trace(max_calls=5))


class Reply(ScriptedClient):
    def __init__(self, answer, quote):
        super().__init__()
        self.answer, self.quote = answer, quote

    def prompt(self, prompt):
        self.calls.append((prompt, None, {}))
        return json.dumps({'answer': self.answer, 'quote': self.quote})


def test_whole_card_uses_only_unused_legacy_context_budget():
    from zotero_summarizer.services.library._paper_read_meta import artifact_text

    result = run(ScriptedClient())
    cap = len(artifact_text(result['content'], max_chars=20000, review={'digest': result['digest']}))
    client = ScriptedClient()
    withheld = ask(result, client, cap=cap)
    assert withheld['card_visibility'] == 'whole_card_over_budget'
    assert 'AI interpretation draft (not original evidence)' not in client.calls[-1][0]
    visible = ask(result, client, cap=20000)
    assert visible['card_visibility'] == 'whole_card'
    assert json.dumps(result['interpretation']['items'], ensure_ascii=False) in client.calls[-1][0]


def test_card_visibility_does_not_make_card_only_answer_warrant():
    result = run(ScriptedClient())
    result['interpretation']['items'][0]['studied_context'] = 'Unique hypothetical gamma delta epsilon clinical universe'
    from zotero_summarizer.services.library.interpretation import scientific_claims
    result['interpretation']['claims'] = scientific_claims(result['interpretation']['items'], result['digest'])
    quote = 'The sample retained alpha. The studied cohort was adults. The design was observational.'
    client = Reply(result['interpretation']['items'][0]['studied_context'], quote)
    parsed = ask(result, client)
    assert parsed['grounding'] == 'legacy_authority_rejected'
    assert parsed['response'] == {'answer': None, 'abstained': True, 'quote': None}
    assert parsed['raw'] == json.dumps({'answer': client.answer, 'quote': quote})


def test_supplied_visibility_cannot_reinstate_truncated_original_quote():
    result = run(ScriptedClient())
    quote = 'The sample retained alpha. The studied cohort was adults. The design was observational.'
    parsed = ask(result, Reply('The sample retained alpha', quote), cap=25)
    assert parsed['response']['abstained']
    assert parsed['grounding'] == 'legacy_authority_rejected'


def test_original_grounded_answer_remains_accepted():
    result = run(ScriptedClient())
    quote = 'The sample retained alpha. The studied cohort was adults. The design was observational.'
    parsed = ask(result, Reply('The sample retained alpha', quote))
    assert parsed['response']['answer'] == 'The sample retained alpha'
    assert parsed['grounding'] == 'accepted'


def test_saved_record_reaches_shared_native_renderer_and_audit_before_publication(tmp_path, monkeypatch):
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library import _paper_read_html
    from zotero_summarizer.services.library._interpretation_html import section
    from zotero_summarizer.services.zotero._notes import build_digest_note_html, DIGEST_NOTE_MARKER

    saved = tmp_path / 'review.json'
    saved.write_text(json.dumps(run(ScriptedClient())))
    record = json.loads(saved.read_text())
    shared = section(record, record['digest'])
    native = build_digest_note_html(PaperDigest.model_validate(record['digest']), interpretation=record)
    assert shared in native
    assert native.endswith(f'{DIGEST_NOTE_MARKER};version=1</em></p>')
    directory, staging = tmp_path / 'published', tmp_path / 'staging'
    directory.mkdir()
    staging.mkdir()
    observed = []
    original = _paper_read_html._audit_presentation

    def audit(html_text, content, figures):
        assert not (directory / 'paper_presentation.html').exists()
        assert shared in html_text
        observed.append(html_text)
        return original(html_text, content, figures)

    monkeypatch.setattr(_paper_read_html, '_audit_presentation', audit)
    output = _paper_read_html.write_outputs(directory, record['content'], staging=staging,
                                           digest=record['digest'], interpretation=record)
    assert output['audit']['status'] == 'passed'
    assert (directory / 'paper_presentation.html').read_text() == observed[0]
    assert json.loads((directory / 'paper_audit.json').read_text())['blocking'] == []
    assert shared.count('<h3>') == 3
    assert shared.count(CARD['finding']) == 1


def test_scientific_receipt_binding_precedes_mutable_presentation_projection(monkeypatch):
    from zotero_summarizer.services.library import _interpretation_html, _paper_read_brief, _paper_read_html

    record = run(ScriptedClient())
    observed = []
    original_binding, original_brief = _interpretation_html.section, _paper_read_brief.brief_html

    def binding(*args, **kwargs):
        html = original_binding(*args, **kwargs)
        observed.append('original_receipt_bound')
        return html

    def presentation(*args, **kwargs):
        assert observed == ['original_receipt_bound']
        return original_brief(*args, **kwargs)

    monkeypatch.setattr(_interpretation_html, 'section', binding)
    monkeypatch.setattr(_paper_read_brief, 'brief_html', presentation)
    _paper_read_html._render_presentation(record['content'], record['digest'], interpretation=record)
    assert observed == ['original_receipt_bound']


def test_mismatched_current_digest_refuses_both_actual_renderers():
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._paper_read_html import _render_presentation
    from zotero_summarizer.services.zotero._notes import build_digest_note_html

    record = run(ScriptedClient())
    other = deepcopy(record['digest'])
    other['tldr'] = 'A different current review'
    with pytest.raises(ValueError, match='Renderer digest differs'):
        _render_presentation(record['content'], other, interpretation=record)
    with pytest.raises(ValueError, match='Renderer digest differs'):
        build_digest_note_html(PaperDigest.model_validate(other), interpretation=record)


def test_optional_transport_failure_does_not_discard_independently_accepted_base():
    class OptionalTransportFailure(ScriptedClient):
        def pydantic_prompt(self, *, prompt, **kwargs):
            if 'interpretation[' in prompt:
                raise ConnectionError('synthetic optional verifier unavailable')
            return super().pydantic_prompt(prompt=prompt, **kwargs)

    record = run(OptionalTransportFailure())
    assert record['digest']['key_findings'] == [CARD['finding']]
    assert record['interpretation']['status'] == 'unavailable'
    assert record['interpretation']['failure_type'] == 'APIError'


def test_non_object_generation_still_gets_existing_single_parse_retry():
    class NonObjectThenValid(ScriptedClient):
        def __init__(self):
            super().__init__()
            self.first = True

        def pydantic_prompt(self, *, pydantic_model, **kwargs):
            from zotero_summarizer.models import PaperDigest
            if issubclass(pydantic_model, PaperDigest) and self.first:
                self.first = False
                self.calls.append(('invalid JSON object', pydantic_model, kwargs))
                return '[]'
            return super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)

    client = NonObjectThenValid()
    assert run(client)['interpretation']['status'] == 'accepted'
    assert len(client.calls) == 4
