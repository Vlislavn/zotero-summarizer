"""Admission, binary preservation and immutable replay oracles (parent executes)."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.method_validation import support_run
from tools.method_validation.support import (
    Config, SupportCase, admit_support_case, digest, encoded, gold_identity, loads, sha,
)
from test_support_fixtures import fixture, rows_at, update_input, validated, write_json


def test_unicode_repeated_original_occurrence_and_canonical_identity(tmp_path):
    cfg, bundle, case, _ = fixture(tmp_path)
    original = bundle['sources'][1]['text']
    admitted = admit_support_case(case, original)
    assert admitted.span_start == original.rindex(case['excerpt']) != original.index(case['excerpt'])
    assert original.encode().rindex(case['excerpt'].encode()) != admitted.span_start
    assert gold_identity(admitted, original) == gold_identity(admitted, original.upper())
    assert admitted.source_sha256 != sha(original.upper().encode())
    assert support_run.prepare(validated(cfg))[2] == {digest(case): admitted}


@pytest.mark.parametrize('mutation', [
    {'source_sha256': '0' * 64}, {'excerpt_sha256': '0' * 64}, {'span_start': 0},
    {'span_start': True}, {'span_end': 9999}, {'schema_version': True}, {'excerpt': 'summary'},
    {'criterion': '   '}, {'paper_key': 'unknown'}, {'gold_label': 'supports'}, {'private_notes': 'secret'},
])
def test_invalid_original_or_projection_is_rejected_before_http_or_writes(tmp_path, mutation):
    cfg, bundle, case, _ = fixture(tmp_path, kind='native')
    bundle['cases'][0].update(mutation)
    update_input(cfg, bundle)
    with pytest.raises((ValueError, ValidationError)):
        support_run.run(validated(cfg), encoded(cfg), transport=object())
    assert not (tmp_path / 'selected').exists()


def test_source_substitution_and_wrong_input_hash_are_not_negative_labels(tmp_path):
    cfg, bundle, _, _ = fixture(tmp_path)
    bundle['sources'][1]['text'] = 'Generated map notes, not original.'
    update_input(cfg, bundle)
    with pytest.raises(ValueError, match='source SHA'):
        support_run.prepare(validated(cfg))
    cfg['inputs']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='SHA'):
        support_run.prepare(validated(cfg))


@pytest.mark.parametrize('field,value', [
    ('max_calls', True), ('max_calls', 0), ('max_calls', 10001),
    ('max_input_bytes', 67108865), ('max_response_bytes', 1048577),
    ('max_prompt_chars', 0), ('idle_timeout_seconds', 601), ('idle_timeout_seconds', True),
    ('probability_sum_tolerance', float('nan')), ('max_cost', float('inf')),
    ('min_pairs', 79), ('min_papers', 19), ('option_order', ['supports', 'contradicts', 'insufficient_evidence']),
    ('reference', 'unknown'), ('generator_handle', 'human-only'), ('project_root', 'relative'),
])
def test_invalid_configuration_has_no_fallback(tmp_path, field, value):
    cfg, _, _, _ = fixture(tmp_path)
    cfg[field] = value
    with pytest.raises(ValidationError):
        validated(cfg)


@pytest.mark.parametrize('kind', ['cross_family', 'canonical_alias', 'holdout_calibration', 'late_calibration', 'split_hash'])
def test_dev_holdout_firewall(tmp_path, kind):
    cfg, bundle, _, _ = fixture(tmp_path)
    if kind == 'cross_family':
        bundle['sources'][0]['family'] = 'holdout-family'
    elif kind == 'canonical_alias':
        bundle['sources'][0]['text'] = bundle['sources'][1]['text'].upper()
    elif kind == 'holdout_calibration':
        cfg['calibration']['development_families'] = ['holdout-family']
    elif kind == 'late_calibration':
        cfg['calibration']['frozen_at'] = bundle['holdout_opened_at']
    else:
        cfg['calibration']['split_sha256'] = '0' * 64
    update_input(cfg, bundle)
    with pytest.raises(ValueError):
        support_run.prepare(validated(cfg))


def test_gold_matches_normalized_source_criterion_identity_not_array_position(tmp_path):
    cfg, bundle, case, _ = fixture(tmp_path)
    original = bundle['sources'][1]['text']
    second = {**case, 'pair_id': 'second', 'criterion': 'Different criterion'}
    bundle['cases'].append(second)
    bundle['gold'].insert(0, {'identity': gold_identity(SupportCase(**second), original),
                            'label': 'contradicts', 'human_provenance': 'public synthetic'})
    update_input(cfg, bundle)
    owner, _ = support_run.run(validated(cfg), encoded(cfg))
    rows = rows_at(owner)
    assert {r['gold'] for r in rows if r['pair_id'] == 'public-pair'} == {'supports'}
    assert {r['gold'] for r in rows if r['pair_id'] == 'second'} == {'contradicts'}


@pytest.mark.parametrize('verdict,expected', [(None, True), ('confirm', True), (' reject ', False), ('other', True)])
def test_actual_legacy_omission_default_and_explicit_rejection_stay_binary(tmp_path, verdict, expected):
    cfg, _, _, _ = fixture(tmp_path, verifier=verdict)
    owner, result = support_run.run(validated(cfg), encoded(cfg))
    row = next(r for r in rows_at(owner) if r['arm_id'] == 'verifier')
    assert row['support'] is expected and row['baseline_native']['verdict'] == verdict
    assert row['readout'] is None and row['capability'] == 'binary_support'
    assert result['arms']['verifier']['per_class'] is None
    assert result['arms']['verifier']['three_way_brier'] is None
    mini = next(r for r in rows_at(owner) if r['arm_id'] == 'minicheck')
    assert mini['baseline_native']['score'] == 0.75 and mini['support'] is True
    assert support_run.reopen(owner) == result


def test_missing_baseline_artifact_and_missing_row_are_unavailable_not_reject(tmp_path):
    cfg, _, _, _ = fixture(tmp_path)
    Path(cfg['baselines'][0]['artifact']['path']).unlink()
    mini_path = Path(cfg['baselines'][1]['artifact']['path'])
    artifact = loads(mini_path.read_bytes())
    artifact['rows'] = []
    cfg['baselines'][1]['artifact'] = write_json(mini_path, artifact)
    owner, result = support_run.run(validated(cfg), encoded(cfg))
    assert all(r['support'] is None and r['status'] == 'unavailable'
               for r in rows_at(owner) if r['capability'] == 'binary_support')
    assert result['winner'] is None
    assert support_run.reopen(owner) == result


def test_missing_minicheck_threshold_keeps_scalar_without_synthetic_binary_decision(tmp_path):
    cfg, _, _, _ = fixture(tmp_path)
    cfg['baselines'][1]['threshold'] = None
    cfg['calibration'] = None
    owner, result = support_run.run(validated(cfg), encoded(cfg))
    row = next(r for r in rows_at(owner) if r['arm_id'] == 'minicheck')
    assert row['status'] == 'unavailable' and row['support'] is None
    assert row['baseline_native']['score'] == 0.75 and row['readout'] is None
    assert result['arms']['minicheck']['binary_score_brier']['mean'] == 0.0625
    assert result['arms']['minicheck']['three_way_brier'] is None
    assert support_run.reopen(owner) == result


@pytest.mark.parametrize('mutation', ['owner', 'model', 'input', 'binary', 'duplicate'])
def test_foreign_or_mismatched_baseline_is_admission_error(tmp_path, mutation):
    cfg, _, _, _ = fixture(tmp_path)
    path = Path(cfg['baselines'][0]['artifact']['path'])
    artifact = loads(path.read_bytes())
    if mutation == 'owner':
        artifact['owner_sha256'] = '0' * 64
    elif mutation == 'model':
        artifact['model'] = 'wrong-model'
    elif mutation == 'input':
        artifact['rows'][0]['case_sha256'] = '0' * 64
    elif mutation == 'binary':
        artifact['rows'][0]['score'] = 0.5
    else:
        artifact['rows'] *= 2
    cfg['baselines'][0]['artifact'] = write_json(path, artifact)
    with pytest.raises(ValueError):
        support_run.prepare(validated(cfg))


@pytest.mark.parametrize('budget,value', [('max_prompt_chars', 1), ('max_request_bytes', 1),
                                          ('max_calls', 1), ('max_work_units', 1), ('max_input_bytes', 1)])
def test_whole_workload_preflight_zero_calls_and_zero_output(tmp_path, budget, value):
    cfg, bundle, case, _ = fixture(tmp_path, kind='native')
    bundle['cases'].append({**case, 'pair_id': 'second', 'criterion': 'Different question'})
    update_input(cfg, bundle)
    cfg[budget] = value
    with pytest.raises(ValueError):
        support_run.run(validated(cfg), encoded(cfg), transport=object())
    assert not (tmp_path / 'selected').exists()


def test_owner_exclusive_corruption_and_partial_attempt_refused(tmp_path):
    cfg, _, _, _ = fixture(tmp_path)
    owner, result = support_run.run(validated(cfg), encoded(cfg))
    assert support_run.reopen(owner) == result
    with pytest.raises(FileExistsError):
        support_run.run(validated(cfg), encoded(cfg))
    (owner / 'terminal-0.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='corrupt'):
        support_run.reopen(owner)
    (owner / 'ACK').unlink()
    with pytest.raises(FileNotFoundError):
        support_run.reopen(owner)


def test_reopen_uses_owned_snapshots_not_external_inputs_or_new_publication(tmp_path, monkeypatch):
    cfg, bundle, _, path = fixture(tmp_path)
    owner, result = support_run.run(validated(cfg), encoded(cfg))
    for ref in [cfg['inputs'], *(b['artifact'] for b in cfg['baselines']),
                *(r['terminal'] for r in bundle['recordings'])]:
        Path(ref['path']).unlink()
    path.unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError('inspect must not publish')
    monkeypatch.setattr(support_run, 'publish', forbidden)
    assert support_run.reopen(owner) == result


def test_writer_write_then_raise_is_not_ack_or_replay_permission(tmp_path, monkeypatch):
    cfg, _, _, _ = fixture(tmp_path)
    original_publish = support_run.publish
    def broken(path, raw):
        original_publish(path, raw)
        if path.name == 'ACK':
            raise OSError('required writer failed after publishing readable bytes')
    monkeypatch.setattr(support_run, 'publish', broken)
    with pytest.raises(OSError):
        support_run.run(validated(cfg), encoded(cfg))
    owner = tmp_path / 'selected/data/support_comparison/public-attempt'
    assert (owner / 'ACK').exists() and (owner / 'FAILED').exists()
    with pytest.raises(ValueError, match='failed owner'):
        support_run.reopen(owner)


def test_all_blocked_missing_human_and_null_resources_cannot_select_winner(tmp_path):
    cfg, bundle, _, _ = fixture(tmp_path, kind='blocked')
    cfg['arms'][0]['blocked_reason'] = 'TypeSafe access and author-server protocol unknown'
    bundle['gold'][0]['human_provenance'] = None
    update_input(cfg, bundle)
    owner, result = support_run.run(validated(cfg), encoded(cfg), transport=object())
    assert next(r for r in rows_at(owner) if r['arm_id'] == 'native')['status'] == 'blocked'
    assert result['winner'] is None and result['action'] == 'retain_baseline'
    assert result['conclusion'] in ('no-go', 'inconclusive')
    assert result['arms']['native']['latency_seconds']['mean'] is None
    assert result['arms']['native']['operational_error'] is None
    assert result['arms']['native']['evaluated_pairs'] == 0
    for metric in ('cold_seconds', 'peak_memory_bytes', 'cost', 'physical_retries', 'forward_passes'):
        assert result['arms']['native'][metric] is None


def test_artifact_reopen_cannot_relabel_a_native_failure_even_with_rehashed_inventory(tmp_path):
    cfg, _, _, _ = fixture(tmp_path, raw=b'not JSON')
    owner, _ = support_run.run(validated(cfg), encoded(cfg))
    path = owner / 'pair-0.json'
    row = loads(path.read_bytes())
    row.update(status='decision', support=True)
    path.write_bytes(encoded(row))
    manifest = loads((owner / 'manifest.json').read_bytes())
    manifest['files']['pair-0.json'] = sha(path.read_bytes())
    (owner / 'manifest.json').write_bytes(encoded(manifest))
    (owner / 'ACK').write_bytes(sha((owner / 'manifest.json').read_bytes()).encode())
    with pytest.raises(ValueError):
        support_run.reopen(owner)
