"""Machine I/O, lineage, taxonomy and complete-workload boundaries."""
import json
import pytest

from test_collection_fixtures import LABELS, bind, bundle, identity, response, save
from tools.method_validation.collections import Config, Inputs, Truth, admit, payload, project
from tools.method_validation.collections_run import baseline_owner, prepare, run


def prepared(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    return config, inputs, truth, path


def artifact(tmp_path, config, inputs, *, names=None, has_corpus=True):
    native = {'affinity_score': 0.2, 'positive_similarity': 0.2, 'negative_similarity': 0.0,
              'matched_goal': '', 'matched_goal_similarity': 0.0, 'top_similar_items': [],
              'has_corpus': has_corpus, 'suggested_collections': ['Project'] if names is None else names}
    reference = {**inputs['papers'][0], 'paper_id': 'reference', 'family': 'reference-family',
                 'canonical_ids': ['doi:reference'], 'title': 'Earlier reference', 'abstract': 'Development evidence',
                 'split': 'development', 'observed_at': '2026-07-15T00:00:00Z'}
    data = {'model': 'fixture-embedding', 'owner_sha256': baseline_owner(), 'model_artifact_sha256': '1' * 64,
            'frozen_at': '2026-08-01T00:00:00Z', 'reference_papers': [reference] if has_corpus else [],
            'rows': [{'case_sha256': identity(p), 'observed_model': 'fixture-embedding', 'readout': native}
                     for p in inputs['papers']]}
    config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    return data


def test_payload_contains_only_question_evidence_and_no_assignment_metadata(tmp_path):
    config, inputs, truth, _ = prepared(tmp_path)
    truth['labels'][0]['human_provenance'] = 'PRIVATE_GOLD_SENTINEL'
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    admitted = admit(cfg, Inputs.model_validate(inputs), Truth.model_validate(truth))
    papers, _, nodes, paths, questions = admitted
    raw = payload(next(iter(papers.values())), 'B', (nodes, paths, Inputs.model_validate(inputs).tree), cfg)
    data = json.loads(raw)
    assert set(data) == {'state', 'questions'}
    assert list(data['questions']['decision']['criteria']) == list(LABELS)
    assert 'PRIVATE_GOLD_SENTINEL' not in raw.decode()
    assert 'human_provenance' not in raw.decode() and 'canonical_ids' not in raw.decode()
    assert 'family-0' not in raw.decode() and 'holdout' not in raw.decode()
    assert 'Topic' in data['state'] and 'Project' in data['state']
    assert questions == ('A', 'B')


@pytest.mark.parametrize('change', ['cycle', 'parent', 'duplicate', 'workflow', 'empty', 'frozen',
                                    'gold_unknown', 'gold_workflow', 'gold_duplicate', 'text_duplicate',
                                    'canonical_leak', 'family_leak', 'gold_index', 'example_leak'])
def test_structural_or_firewall_violation_refuses_before_effects(tmp_path, change):
    config, inputs, truth, _ = prepared(tmp_path)
    if change == 'cycle': inputs['tree']['nodes'][0]['parent'] = 'B'
    if change == 'parent': inputs['tree']['nodes'][0]['parent'] = 'missing'
    if change == 'duplicate': inputs['tree']['nodes'][1]['key'] = 'A'
    if change == 'workflow': inputs['tree']['selected_keys'] = ['W']
    if change == 'empty': inputs['tree']['selected_keys'] = []
    if change == 'frozen': inputs['tree']['frozen_at'] = '2026-09-20T00:00:00Z'
    if change == 'gold_unknown': truth['labels'][0]['memberships'] = ['missing']
    if change == 'gold_workflow': truth['labels'][0]['memberships'] = ['W']
    if change == 'gold_duplicate': truth['labels'][0]['memberships'] = ['A', 'A']
    if change == 'text_duplicate': inputs['papers'][1].update(title=' PAPER 0 ', abstract=' original  ABSTRACT 0 ')
    if change == 'canonical_leak': inputs['papers'][1]['canonical_ids'] = ['DOI:0']
    if change == 'family_leak': inputs['papers'][1].update(family='family-0', split='development')
    if change == 'gold_index': truth['labels'][0]['identity'] = 'f' * 64
    if change == 'example_leak':
        inputs['tree']['examples'] = [{'paper': inputs['papers'][0], 'node_key': 'A', 'belongs': True}]
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(ValueError): prepare(cfg)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('budget,value', [('max_calls', 1), ('max_work_units', 1),
                                        ('max_snapshot_bytes', 10)])
def test_whole_work_reservation_refuses_before_owner(tmp_path, budget, value):
    config, inputs, truth, path = prepared(tmp_path)
    if budget == 'max_calls':
        config['arms'][0].update(kind='native', endpoint='https://example.invalid/v1/systemone',
            eligibility='qualified', access='qualified', runtime_sha256='0'*64, checkpoint_sha256='0'*64,
            quantization='fixture', model_source='fixture', licence='fixture', context_verified=True,
            forward_passes_bound=1)
        inputs['recordings'] = []
        config.update(public_input_consent=True, external_guard='test-only')
    config[budget] = value
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('kind', ['missing', 'oversized'])
def test_missing_and_oversized_complete_inputs_remain_unscored(tmp_path, kind):
    config, inputs, truth, path = prepared(tmp_path)
    if kind == 'missing':
        inputs['papers'][0]['abstract'] = None
        truth['labels'][0]['identity'] = identity(inputs['papers'][0])
        inputs['recordings'] = inputs['recordings'][2:]
    else:
        config['max_prompt_chars'] = 1
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    _, report = run(cfg, path.read_bytes())
    statuses = report['arms']['recorded']['question_statuses']
    assert statuses['missing_input' if kind == 'missing' else 'input_too_large'] == (2 if kind == 'missing' else 6)
    assert report['arms']['recorded']['complete_papers'] == (2 if kind == 'missing' else 0)


@pytest.mark.parametrize('raw,status', [(b'{', 'invalid_response'), (b'\xff', 'invalid_response'),
    (response(model='wrong'), 'model_mismatch'),
    (response(probabilities={'belongs': 0.9, 'does_not_belong': 0.9, 'insufficient_evidence': 0}), 'invalid_response'),
    (response(label='insufficient_evidence'), 'decision')])
def test_native_wire_outcomes_are_not_silent_negative_memberships(tmp_path, raw, status):
    config, _, _, _ = prepared(tmp_path)
    cfg = Config.model_validate(config)
    result, readout = project(raw, cfg.arms[0], cfg)
    assert result == status
    if status == 'decision': assert readout['label'] == 'insufficient_evidence'


def test_choice_does_not_need_to_equal_argmax(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    cfg = Config.model_validate(config)
    raw = response('belongs', probabilities={'belongs': .1, 'does_not_belong': .8, 'insufficient_evidence': .1})
    status, readout = project(raw, cfg.arms[0], cfg)
    assert status == 'decision' and readout['label'] == 'belongs'
    assert readout['probabilities'] == [.1, .8, .1]
    with pytest.raises(ValueError): project(raw, cfg.arms[0], cfg, 'blocked')


@pytest.mark.parametrize('names,status,keys', [(['Project'], 'decision', ['B']),
    ([], 'decision', []), (['Inbox', 'Project'], 'decision', ['B']),
    (['missing'], 'invalid_response', None), (['Project', 'Project'], 'invalid_response', None)])
def test_actual_original_name_only_baseline_is_resolved_without_invented_scores(tmp_path, names, status, keys):
    config, inputs, truth, path = prepared(tmp_path)
    artifact(tmp_path, config, inputs, names=names)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, report = run(cfg, path.read_bytes())
    row = json.loads((owner / 'case-2.json').read_bytes())
    assert row['status'] == status and row['selected_keys'] == keys
    assert row['baseline_native']['readout']['suggested_collections'] == names
    assert row['readout'] is None
    assert report['arms']['similarity']['brier'] is None


def test_duplicate_name_including_workflow_cannot_be_disambiguated_with_gold(tmp_path):
    config, inputs, truth, path = prepared(tmp_path)
    inputs['tree']['nodes'][2]['name'] = 'Project'
    artifact(tmp_path, config, inputs)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    _, report = run(cfg, path.read_bytes())
    assert report['arms']['similarity']['question_statuses'] == {'invalid_response': 3}
    assert report['arms']['similarity']['exact_set_accuracy'] is None


def test_absent_corpus_never_means_confident_no_match(tmp_path):
    config, inputs, truth, path = prepared(tmp_path)
    artifact(tmp_path, config, inputs, names=[], has_corpus=False)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    _, report = run(cfg, path.read_bytes())
    assert report['arms']['similarity']['question_statuses'] == {'unavailable': 3}
    assert report['arms']['similarity']['empty_similarity_retrieval'] == 0


def test_shortlist_is_the_same_for_every_arm_and_recall_is_separate(tmp_path):
    config, inputs, truth, path = prepared(tmp_path)
    inputs['tree']['selected_keys'] = ['B']
    inputs['tree']['selection_provenance'] = 'SYNTHETIC frozen non-gold selector'
    inputs['recordings'] = inputs['recordings'][1::2]
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    _, report = run(cfg, path.read_bytes())
    assert report['shortlist_recall']['mean'] == .75
    assert report['selected_question_keys'] == ['B']
    assert report['arms']['recorded']['confirmed_native_no_match'] == 0
    assert report['arms']['recorded']['exact_set_accuracy'] == pytest.approx(2/3)
