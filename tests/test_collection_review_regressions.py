"""Independent questions, original nested paths, frozen lineage and transport replay."""
import json

import httpx
import pytest

from test_collection_boundaries import artifact
from test_collection_fixtures import bind, bundle, encoded, response, save, sha
from test_collection_native import native
from test_collection_reservations import development_reference
from tools.method_validation.collections import Config, project
from tools.method_validation.collections_report import readable, report
from tools.method_validation.collections_run import prepare, reopen, run


def test_one_oversized_question_preserves_other_questions_and_original_baseline(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    inputs['tree']['nodes'][1]['description'] = 'x' * 13000
    artifact(tmp_path, config, inputs)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    assert result['arms']['recorded']['question_statuses'] == {'decision': 3, 'input_too_large': 3}
    assert result['arms']['recorded']['question_coverage'] == .5
    assert result['arms']['recorded']['complete_papers'] == 0
    assert result['arms']['similarity']['complete_papers'] == 3
    assert len(list(owner.glob('terminal-*.bin'))) == 3
    assert reopen(owner) == result


@pytest.mark.parametrize('collision', [False, True])
def test_original_nested_baseline_paths_resolve_without_guessing_collisions(tmp_path, collision):
    config, inputs, truth, path = bundle(tmp_path)
    if collision:
        inputs['tree']['nodes'].append({'key': 'C', 'name': 'Topic > Project', 'parent': None,
                                       'role': 'workflow', 'description': 'Ambiguous literal name'})
    artifact(tmp_path, config, inputs, names=['Topic > Project'])
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    row = json.loads((owner / 'case-2.json').read_bytes())
    assert row['status'] == ('invalid_response' if collision else 'decision')
    assert row['selected_keys'] == (None if collision else ['B'])
    assert row['baseline_native']['readout']['suggested_collections'] == ['Topic > Project']
    assert reopen(owner) == result


@pytest.mark.parametrize('names,status,excluded', [
    (['Topic > Inbox', 'Topic > Project'], 'decision', 3),
    (['Project', 'Topic > Project'], 'invalid_response', 0)])
def test_nested_workflow_exclusion_and_duplicate_alias_refusal(tmp_path, names, status, excluded):
    config, inputs, truth, path = bundle(tmp_path)
    inputs['tree']['nodes'][2]['parent'] = 'A'
    artifact(tmp_path, config, inputs, names=names)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    assert result['arms']['similarity']['question_statuses'] == {status: 3}
    assert result['arms']['similarity']['workflow_suggestions_excluded'] == excluded
    assert reopen(owner) == result


@pytest.mark.parametrize('owner', ['taxonomy', 'baseline'])
def test_reference_must_strictly_precede_its_frozen_owner(tmp_path, owner):
    config, inputs, truth, path = bundle(tmp_path)
    reference = development_reference()
    reference['observed_at'] = inputs['tree']['frozen_at']
    if owner == 'taxonomy':
        inputs['tree']['description_sources'] = [reference]
    else:
        data = artifact(tmp_path, config, inputs)
        data['reference_papers'] = [reference]
        config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError, match='reference|leakage'):
        run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


def test_native_replay_cannot_promote_a_recorded_http_failure_to_decision(tmp_path):
    cfg, path = native(tmp_path)
    owner, _ = run(cfg, path.read_bytes(), transport=httpx.MockTransport(
        lambda _: httpx.Response(503, content=response())))
    assert reopen(owner)['arms']['recorded']['question_statuses'] == {'unavailable': 6}
    row = json.loads((owner / 'case-0.json').read_bytes())
    row['telemetry']['terminal_status'] = 'received'
    row['status'], row['readout'] = project((owner / row['terminal']).read_bytes(), cfg.arms[0], cfg)
    row['label'] = row['readout']['label']
    (owner / 'case-0.json').write_bytes(encoded(row))
    rows = [json.loads((owner / f'case-{i}.json').read_bytes()) for i in range(9)]
    inputs, truth, *_ = prepare(cfg)
    timings = json.loads((owner / 'paper-times.json').read_bytes())
    result = report(rows, cfg, inputs, truth, timings=timings)
    (owner / 'report.json').write_bytes(encoded(result))
    (owner / 'report.md').write_bytes(readable(result).encode())
    manifest = json.loads((owner / 'manifest.json').read_bytes())
    manifest['files'] = {p.name: sha(p.read_bytes()) for p in owner.iterdir()
                         if p.name not in ('ACK', 'manifest.json')}
    (owner / 'manifest.json').write_bytes(encoded(manifest))
    (owner / 'ACK').write_text(sha((owner / 'manifest.json').read_bytes()))
    with pytest.raises(ValueError, match='transport'):
        reopen(owner)


@pytest.mark.parametrize('entry', ['adapter', 'collection_evaluate'])
def test_trickling_native_response_cannot_escape_the_dispatch_budget(tmp_path, monkeypatch, entry):
    import tools.method_validation.decision_adapter as wire
    cfg, _ = native(tmp_path)
    cfg = Config.model_validate({**cfg.model_dump(), 'max_run_seconds': 1.0 if entry == 'adapter' else 3600.0})
    clock, consumed, retained = [0.0], [], []
    monkeypatch.setattr(wire, 'monotonic', lambda: clock[0])
    raw = response()
    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for start in range(0, len(raw), 10):
                clock[0] += .4
                consumed.append(start)
                yield raw[start:start + 10]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Trickle()))
    if entry == 'adapter':
        adapter = wire.NativeDecisionAdapter(cfg.arms[0], cfg, projector=project, transport=transport)
        reply = adapter(b'{}', retained.append, timeout=1.0)
        status, readout, telemetry = reply.status, reply.readout, reply.telemetry
    else:
        from tools.method_validation.collections_run import evaluate
        row = {}
        evaluate(row, cfg.arms[0], cfg, b'{}', retained.append, transport=transport, remaining=1.0)
        status, readout, telemetry = row['status'], row['readout'], row['telemetry']
    assert status == 'timeout'
    assert readout is None and telemetry['raw_complete'] is False
    assert len(consumed) == 3 and retained == [raw[:30]]


def test_all_scored_gold_joins_by_identity_after_nontrivial_reordering(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    truth['labels'] = [truth['labels'][2], truth['labels'][0], truth['labels'][1]]
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    rows = [json.loads(p.read_bytes()) for p in owner.glob('case-*.json')]
    actual = {r['paper_id']: r['gold_memberships'] for r in rows}
    assert actual == {'0': ['A', 'B'], '1': [], '2': ['B']}
    assert result['arms']['recorded']['exact_set_accuracy'] == 1
    assert reopen(owner) == result


def test_reused_admission_enforces_configured_paper_cap_before_owner(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    config['max_cases'] = 2
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError, match='bounded papers'):
        run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()
