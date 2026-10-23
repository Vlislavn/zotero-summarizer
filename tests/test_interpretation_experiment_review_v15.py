"""Source-review counterexamples: constructed protocol fixtures, not scientific judgments."""
import json

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, ScriptedClient
from tests.test_interpretation_experiment_core import run
from tests.test_interpretation_experiment_report import decision_fixture


@pytest.mark.parametrize('form', ['dict', 'text', 'typed'])
def test_malformed_optional_only_keeps_one_generation_and_accepted_base(form):
    from zotero_summarizer.models import PaperDigest

    class Provider(ScriptedClient):
        def pydantic_prompt(self, *, pydantic_model, **kwargs):
            value = super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)
            if issubclass(pydantic_model, PaperDigest):
                value['interpretation'][0]['rationale'] = 42
                if form == 'typed':
                    return pydantic_model.model_validate(value)
                if form == 'text':
                    return json.dumps(value)
            return value

    client = Provider()
    result = run(client)
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert result['interpretation']['status'] == 'invalid'
    assert len(client.calls) == 2


@pytest.mark.parametrize('mutation', ['same_count_claim', 'different_valid_passage'])
def test_optional_acceptance_binds_exact_checked_claims_and_passages(mutation):
    from zotero_summarizer.services.library.interpretation import qualify, scientific_claims

    result = run(ScriptedClient())
    card = result['interpretation']
    if mutation == 'same_count_claim':
        card['items'][0]['studied_context'] = 'Unique hypothetical gamma delta epsilon clinical universe'
        card['claims'] = scientific_claims(card['items'], result['digest'])
    else:
        from zotero_summarizer.services.faithbench._corpus import chunk_text
        from zotero_summarizer.services.library.interpretation import selected_original
        assert len(chunk_text(selected_original(result))) > 1
        card['checks']['checks'][0]['evidence'] = [1]
    with pytest.raises(ValueError):
        qualify(result)


def test_mismatched_record_content_is_rejected_at_common_boundary():
    from zotero_summarizer.services.library.interpretation import qualify

    result = run(ScriptedClient())
    result['content']['full_text'] = 'Another paper, not the qualified extraction.'
    with pytest.raises(ValueError):
        qualify(result)


def test_external_renderer_content_cannot_disagree_with_qualified_record(tmp_path):
    from zotero_summarizer.services.library._paper_read_html import write_outputs

    result = run(ScriptedClient())
    directory, staging = tmp_path / 'published', tmp_path / 'staging'
    directory.mkdir()
    staging.mkdir()
    published = directory / 'paper_presentation.html'
    published.write_text('previous valid presentation')
    with pytest.raises(ValueError):
        write_outputs(directory, result['content'] | {'full_text': 'Different paper'},
                      staging=staging, digest=result['digest'], interpretation=result)
    assert published.read_text() == 'previous valid presentation'
    assert not (directory / 'paper_audit.json').exists()


def test_twenty_declared_families_cannot_relabel_two_duplicate_extractions(tmp_path):
    from tests._interpretation_cohort import cohort
    from zotero_summarizer.services.faithbench import _dataset
    from zotero_summarizer.services.faithbench._corpus import sha256_text
    from zotero_summarizer.services.faithbench._interpretation_inputs import Recipe, _cohort

    _, path, recipe, _, _ = cohort(tmp_path)
    meta, items = _dataset.load_benchmark(path.parent / recipe['benchmark'])
    keys = [p.item_key for p in meta.papers if p.item_key != 'TUNE0001']
    texts = {key: 'alpha clinical exemplar' if index < 10 else 'alpha agent exemplar'
             for index, key in enumerate(keys)}
    texts['TUNE0001'] = 'Earlier distinct tuning text'
    papers = [paper.model_copy(update={'text_sha256': sha256_text(texts[paper.item_key]),
                                       'n_chars': len(texts[paper.item_key])}) for paper in meta.papers]
    rows = []
    for item in items:
        update = {'paper_text_sha256': sha256_text(texts[item.paper_item_key])}
        if item.kind == 'qa':
            update.update(gold_answer='alpha', span_start=0, span_end=5)
        else:
            other = keys[10] if item.paper_item_key in keys[:10] else keys[0]
            update.update(source_paper_item_key=other, source_gold_answer=texts[other].removeprefix('alpha '))
        rows.append(item.model_copy(update=update))
    # Existing input invariants stay valid; only within-evaluation extraction independence is violated.
    changed = meta.model_copy(update={'papers': papers})
    for row in rows:
        _dataset._validate_item(row, changed, texts)
    with pytest.raises(ValueError):
        _cohort(Recipe.model_validate_json(json.dumps(recipe)), changed, rows, texts)


@pytest.mark.parametrize('known_failure', ['accuracy', 'review'])
def test_missing_unrelated_judgment_cannot_hide_an_established_no_go(known_failure):
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    metrics['B']['human_complete'] = False
    metrics['B']['review_overclaims'] = [None]
    if known_failure == 'accuracy':
        metrics['C']['supported_accuracy'] = 0.8
        metrics['C']['supported_counts'] = [32, 40]
    else:
        metrics['C']['review_overclaims'] = [True]
    assert _decision(metrics, comparisons, recipe, labels) == 'NO-GO'


def test_exact_five_point_thirty_percent_boundary_uses_assigned_counts():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    for arm, count in [('A', 12), ('B', 12), ('C', 8)]:
        metrics[arm].update(assigned=80, overclaim_lower=count / 80, overclaim_upper=count / 80,
                            overclaim_counts=[count, count])
    for comparison in comparisons.values():
        comparison.update(paper_paired_95_interval=[0.0125, 0.1], relative_95_interval=[0.1, 0.7])
    assert _decision(metrics, comparisons, recipe, labels) == 'GO_TO_LARGER_PILOT'


@pytest.mark.parametrize('unknown,known_failure,expected', [
    ('tokens', True, 'NO-GO'), ('tokens', False, 'INCONCLUSIVE'),
    ('timing', True, 'NO-GO'), ('timing', False, 'INCONCLUSIVE'),
])
def test_resource_gate_breach_survives_unrelated_unknown_observations(unknown, known_failure, expected):
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    if unknown == 'tokens':
        labels.complete_token_accounting = None
        for row in metrics.values():
            row.update(observed_tokens_mean=None, tokens_by_paper={'paper': None})
        metrics['A']['latency_p95'] = 100
        metrics['C']['latency_p95'] = 126 if known_failure else 125
    else:
        labels.controlled_runtime = None
        metrics['C'].update(observed_tokens_mean=126 if known_failure else 125,
                            tokens_by_paper={'paper': 126 if known_failure else 125})
    assert _decision(metrics, comparisons, recipe, labels) == expected


@pytest.mark.parametrize('status', [None, True, 17, [], {}, 'unknown-state'])
def test_malformed_persisted_optional_status_strictly_refuses(status):
    from zotero_summarizer.services.library.interpretation import qualify

    result = json.loads(json.dumps(run(ScriptedClient())))
    result['interpretation'] = {'status': status}
    with pytest.raises(ValueError):
        qualify(result)


@pytest.mark.parametrize('status', [None, True, 17, [], {}, 'unknown-state'])
def test_malformed_optional_status_preserves_qualified_qa_and_both_base_renderers(status, tmp_path):
    from tests.test_interpretation_experiment_consumers import ask, Reply
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._paper_read_html import write_outputs
    from zotero_summarizer.services.zotero._notes import build_digest_note_html, DIGEST_NOTE_MARKER

    result = json.loads(json.dumps(run(ScriptedClient())))
    result['interpretation'] = {'status': status, 'raw_optional': 'UNVERIFIED OPTIONAL TEXT'}
    quote = 'The sample retained alpha. The studied cohort was adults. The design was observational.'
    parsed = ask(result, Reply(PAYLOAD['key_findings'][0], quote))
    assert parsed['card_visibility'] == 'proof_unavailable'
    assert parsed['grounding'] == 'accepted'
    assert parsed['response']['answer'] == PAYLOAD['key_findings'][0]
    native = build_digest_note_html(PaperDigest.model_validate(result['digest']), interpretation=result)
    assert 'proof_unavailable' in native and 'UNVERIFIED OPTIONAL TEXT' not in native
    assert PAYLOAD['key_findings'][0] in native
    assert native.endswith(f'{DIGEST_NOTE_MARKER};version=1</em></p>')
    directory, staging = tmp_path / 'published', tmp_path / 'staging'
    directory.mkdir()
    staging.mkdir()
    output = write_outputs(directory, result['content'], staging=staging, digest=result['digest'], interpretation=result)
    html = (directory / 'paper_presentation.html').read_text()
    assert output['audit']['status'] == 'passed'
    assert 'proof_unavailable' in html and 'UNVERIFIED OPTIONAL TEXT' not in html


def test_card_blind_review_packets_have_separate_source_link_judgment_targets():
    from zotero_summarizer.services.faithbench._interpretation_report import blind_packets
    from zotero_summarizer.services.faithbench._corpus import chunk_text
    from zotero_summarizer.services.library.interpretation import selected_original

    result = run(ScriptedClient())
    record = {'assignment': {'review_blind_id': 'opaque-review', 'card_blind_id': 'independent-card', 'question_blind_ids': {}},
              'review': result, 'questions': []}
    packets = blind_packets([record])
    assert 'card_claims' not in packets['opaque-review']
    cards = [packet for key, packet in packets.items() if key != 'opaque-review']
    assert len(cards) == 1
    assert cards[0]['card_checks'] == result['interpretation']['checks']
    assert cards[0]['card_passages'] == chunk_text(selected_original(result))
    assert cards[0]['card_source'] == result['interpretation']['source']
    assert set(cards[0]['card_claims']) == set(result['interpretation']['claims'])


@pytest.mark.parametrize('optional', [42, True, {'rationale': 'wrong envelope'}, [dict(CARD, rationale=42)] * 6])
def test_optional_transport_delays_only_sidecar_validation_and_keeps_scientific_schema(optional):
    from zotero_summarizer.models.interpretation import InterpretationDigest

    value = InterpretationDigest.model_validate(PAYLOAD | {'interpretation': optional})
    assert value.interpretation == optional
    assert value.key_findings == PAYLOAD['key_findings']
    schema = InterpretationDigest.model_json_schema()
    declared = schema['properties']['interpretation']['anyOf'][0]
    assert declared['maxItems'] == 5
    assert declared['items']['$ref'].endswith('/InterpretationItem')
    assert schema['$defs']['InterpretationItem']['properties']['rationale']['type'] == 'string'
    assert schema['$defs']['InterpretationItem']['additionalProperties'] is False
    with pytest.raises(ValueError):
        InterpretationDigest.model_validate({'interpretation': optional, 'key_findings': 42})


def test_mismatched_optional_proof_is_withheld_without_erasing_qualified_base():
    from tests.test_interpretation_experiment_consumers import ask, Reply
    from zotero_summarizer.services.library._interpretation_html import section

    result = run(ScriptedClient())
    result['interpretation']['items'][0]['studied_context'] = 'Unrelated unverified optional context'
    html = section(result, result['digest'])
    assert 'proof_unavailable' in html
    assert 'Unrelated unverified optional context' not in html
    quote = 'The sample retained alpha. The studied cohort was adults. The design was observational.'
    parsed = ask(result, Reply(PAYLOAD['key_findings'][0], quote))
    assert parsed['card_visibility'] == 'proof_unavailable'
    assert parsed['grounding'] == 'accepted'
    assert parsed['response']['answer'] == PAYLOAD['key_findings'][0]


def test_exact_five_point_accuracy_loss_does_not_fail_inclusive_limit():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    metrics['C'].update(supported_accuracy=0.35, supported_counts=[14, 40])
    for arm in ('A', 'B'):
        metrics[arm].update(supported_accuracy=0.4, supported_counts=[16, 40])
    assert _decision(metrics, comparisons, recipe, labels) == 'GO_TO_LARGER_PILOT'
