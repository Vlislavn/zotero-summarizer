"""Public synthetic priority fixtures, not owner labels or model-quality evidence."""
from tools.method_validation.support import digest, encoded, normalized, sha

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
