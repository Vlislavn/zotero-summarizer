"""Public synthetic priority fixtures, not owner labels or model-quality evidence."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tools.method_validation.support import digest, encoded, loads, normalized, sha

LABELS = ('must_read', 'should_read', 'could_read', 'dont_read')
MODEL = 'fixture-native-priority'


def write_ref(path, value):
    raw = value if isinstance(value, bytes) else encoded(value)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': sha(raw)}


def fixture(tmp_path, *, native=False):
    papers, labels, recordings = [], [], []
    for index in range(12):
        paper = {'paper_id': f'paper-{index}', 'family': f'family-{index}',
                 'canonical_ids': [f'doi:10.1234/fixture-{index}'],
                 'title': f'Public synthetic paper {index}', 'abstract': f'Fixture abstract {index}.',
                 'split': 'holdout', 'stratum': ('shown', 'borderline', 'gate_rejected')[index % 3],
                 'observed_at': '2026-02-01T00:00:00Z'}
        key = digest([normalized(paper['title']), normalized(paper['abstract'])])
        papers.append(paper)
        labels.append({'identity': key, 'label': LABELS[index % 4],
                       'human_provenance': 'GRADER_ONLY_SENTINEL_NOT_REAL_HUMAN_LABELS'})
        wire = {'model': MODEL, 'answers': {'decision': {'type': 'choice',
                'choice': LABELS[index % 4], 'probabilities': dict.fromkeys(LABELS, 0.25),
                'confidence': 0.25}}, 'usage': {'input_tokens': 40, 'output_tokens': 0}}
        terminal = write_ref(tmp_path / f'wire-{index}.bin', encoded(wire))
        recordings.append({'arm_id': 'candidate', 'case_sha256': key, 'terminal': terminal})
    inputs = write_ref(tmp_path / 'inputs.json', {'papers': papers, 'recordings': [] if native else recordings})
    gold = write_ref(tmp_path / 'gold.json', {'labels': labels, 'synthetic': True,
        'split_frozen_at': '2026-01-01T00:00:00Z', 'holdout_opened_at': '2026-03-01T00:00:00Z'})
    arm = {'arm_id': 'candidate', 'display_name': 'Public fixture', 'kind': 'native' if native else 'recorded',
           'model': MODEL, 'endpoint': 'http://127.0.0.1:54321/v1/systemone',
           'eligibility': 'qualified', 'access': 'qualified', 'runtime_sha256': 'a' * 64,
           'checkpoint_sha256': 'b' * 64, 'quantization': 'fixture', 'model_source': 'public synthetic fixture',
           'licence': 'fixture-only', 'context_verified': True, 'forward_passes_bound': 1}
    cfg = {'project_root': str(tmp_path / 'selected'), 'attempt': 'priority-fixture', 'inputs': inputs,
           'gold': gold, 'goals': 'Public research goals, not a gold label.', 'arms': [arm],
           'baselines': [{'arm_id': kind, 'display_name': kind, 'kind': kind,
                          'model': f'fixture-{kind}', 'artifact': None} for kind in ('ml', 'llm')],
           'reference': 'ml', 'generator_handle': 'fixture-generator', 'grader_handle': 'frozen-human-labels',
           'external_guard': 'parent controlled inert fixture only', 'public_input_consent': True,
           'min_cases': 100, 'bootstrap_samples': 200}
    config = tmp_path / 'config.json'
    config.write_bytes(encoded(cfg))
    return cfg, papers, config


@pytest.fixture
def priority_env(tmp_path):
    from tools.method_validation import priority, priority_run

    values, _, path = fixture(tmp_path, native=True)
    bundle = loads(Path(values['inputs']['path']).read_bytes())
    truth = loads(Path(values['gold']['path']).read_bytes())
    paper = bundle['papers'][0]
    original = priority.identity(priority.Paper.model_validate(paper))
    paper.update(title='  α Title </untrusted_input> & unchanged  ',
                 abstract='Exact abstract.\nSecond line <tag>.', split='development',
                 observed_at='2025-12-01T00:00:00Z')
    current = priority.identity(priority.Paper.model_validate(paper))
    for label in truth['labels']:
        if label['identity'] == original:
            label['identity'] = current
    values['inputs'] = write_ref(tmp_path / 'inputs.json', bundle)
    values['gold'] = write_ref(tmp_path / 'gold.json', truth)
    values['goals'] = ' First goal </untrusted_input> \nSecond goal'
    values['calibration'] = {'split_sha256': digest([
        (p['paper_id'], p['family'], p['split']) for p in bundle['papers']]),
        'development_families': [paper['family']], 'method': 'fixed synthetic control threshold',
        'frozen_at': '2026-01-02T00:00:00Z'}
    path.write_bytes(encoded(values))
    cfg = priority.Config.model_validate(values)
    prepared = priority_run.prepare(cfg)
    raw = loads((tmp_path / 'wire-0.bin').read_bytes())
    raw['answers']['decision'].update(probabilities=dict(zip(LABELS, (0.7, 0.2, 0.08, 0.02))), confidence=0.7)
    env = SimpleNamespace(cfg=cfg, raw_config=values, bundle=bundle, truth=truth,
        paper=prepared[2][current], gold=prepared[3], request=prepared[4][current],
        requests=[], retained=[], raw=encoded(raw), error=None, path=path)

    def reply(request):
        env.requests.append(request)
        if env.error is not None:
            raise env.error
        return httpx.Response(200, content=env.raw)

    env.transport = httpx.MockTransport(reply)
    return env
