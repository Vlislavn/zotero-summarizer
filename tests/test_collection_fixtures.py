"""Public synthetic collection comparator inputs; no model or library data."""
from pathlib import Path

from tools.method_validation.support import encoded, digest, normalized, sha

LABELS = ('belongs', 'does_not_belong', 'insufficient_evidence')


def identity(paper):
    return digest([normalized(paper['title'] or ''), normalized(paper['abstract'] or '')])


def bind(root, name, value):
    path = root / name
    raw = value if isinstance(value, bytes) else encoded(value)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': sha(raw)}


def response(label='belongs', model='fixture-native', probabilities=None):
    return encoded({'model': model, 'answers': {'decision': {'type': 'choice', 'choice': label,
        'probabilities': probabilities or {k: float(k == label) for k in LABELS}, 'confidence': 0.99}},
        'usage': {'input_tokens': 5, 'output_tokens': 0}})


def bundle(root):
    papers = [{'paper_id': str(i), 'family': f'family-{i}', 'canonical_ids': [f'doi:{i}'],
               'title': f'Paper {i}', 'abstract': f'Original abstract {i}',
               'split': 'holdout', 'stratum': 'public-fixture', 'observed_at': '2026-09-10T00:00:00Z'}
              for i in range(3)]
    tree = {'nodes': [
        {'key': 'A', 'name': 'Topic', 'parent': None, 'role': 'topical', 'description': 'Topic research'},
        {'key': 'B', 'name': 'Project', 'parent': 'A', 'role': 'project', 'description': 'Specific project'},
        {'key': 'W', 'name': 'Inbox', 'parent': None, 'role': 'workflow', 'description': 'Workflow only'}],
        'frozen_at': '2026-08-01T00:00:00Z', 'description_sources': [], 'examples': []}
    memberships = [('A', 'B'), (), ('B',)]
    labels = [{'identity': identity(p), 'memberships': list(keys),
               'human_provenance': 'SYNTHETIC machine wiring only'} for p, keys in zip(papers, memberships)]
    recordings = []
    for paper, keys in zip(papers, memberships):
        for node in ('A', 'B'):
            unit = digest([identity(paper), node])
            raw = response('belongs' if node in keys else 'does_not_belong')
            recordings.append({'arm_id': 'recorded', 'case_sha256': unit,
                               'terminal': bind(root, f'{unit}.bin', raw)})
    inputs = {'papers': papers, 'tree': tree, 'recordings': recordings}
    truth = {'labels': labels, 'synthetic': True, 'split_frozen_at': '2026-09-01T00:00:00Z',
             'holdout_opened_at': '2026-09-11T00:00:00Z'}
    config = {'project_root': str(root), 'attempt': 'fixture', 'inputs': bind(root, 'inputs.json', inputs),
              'gold': bind(root, 'gold.json', truth),
              'arms': [{'arm_id': 'recorded', 'display_name': 'Recorded', 'kind': 'recorded',
                        'model': 'fixture-native'}],
              'baselines': [{'arm_id': 'similarity', 'display_name': 'Existing similarity',
                             'kind': 'similarity', 'model': 'fixture-embedding', 'artifact': None}],
              'reference': 'similarity', 'generator_handle': 'fixture-generator',
              'grader_handle': 'deterministic-set-v1'}
    config_path = root / 'config.json'
    config_path.write_bytes(encoded(config))
    return config, inputs, truth, config_path


def save(root, config, inputs, truth):
    config = dict(config, inputs=bind(root, 'inputs.json', inputs), gold=bind(root, 'gold.json', truth))
    (root / 'config.json').write_bytes(encoded(config))
    return config


def cli(config_path, *args):
    import subprocess
    import sys
    source = Path(__file__).resolve().parents[1]
    code = f"import sys,runpy;sys.path.insert(0,{str(source)!r});runpy.run_module('tools.method_validation',run_name='__main__')"
    source_args = ['--config', str(config_path)] if config_path is not None else []
    return subprocess.run([sys.executable, '-I', '-B', '-c', code, 'collections', *source_args,
                           *args], capture_output=True, text=True, input='')


def test_fixture_identities_are_nonpositional(tmp_path):
    _, inputs, truth, _ = bundle(tmp_path)
    assert [identity(p) for p in inputs['papers']] == [g['identity'] for g in truth['labels']]
    assert len(set(r['case_sha256'] for r in inputs['recordings'])) == 6
