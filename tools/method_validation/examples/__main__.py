"""Write runnable SYNTHETIC support/priority/collections/faithbench examples into OUT_DIR.

Usage: python -m tools.method_validation.examples OUT_DIR

Public toy data only (no owner data, no model calls, no network). Support, priority and
collections use RECORDED arms, so even ``--run`` replays bundled bytes and writes only under
OUT_DIR. The configs need absolute paths + SHA-256 pins, hence a generator, not static JSON.
These wiring examples carry no scientific meaning (they cannot pick a winner).
"""
import sys
from pathlib import Path

from tools.method_validation.support import (OWNERS, SUPPORT_OPTIONS, SupportCase, digest, encoded,
                                             gold_identity, normalized, sha)

NOTE = 'public synthetic example; not a scientific label'
STAMP = {'split_frozen_at': '2026-01-01T00:00:00Z', 'holdout_opened_at': '2026-03-01T00:00:00Z'}
PRIORITIES = ('must_read', 'should_read', 'could_read', 'dont_read')
MEMBERSHIP = ('belongs', 'does_not_belong', 'insufficient_evidence')


def pin(path: Path, value) -> dict:
    raw = value if isinstance(value, bytes) else encoded(value)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': sha(raw)}


def wire(choice, options, model):
    return encoded({'model': model, 'answers': {'decision': {'type': 'choice', 'choice': choice,
        'probabilities': {o: float(o == choice) for o in options}, 'confidence': 1.0}},
        'usage': {'input_tokens': 10, 'output_tokens': 0}})


def recorded_arm(model, **extra):
    return {'arm_id': 'recorded', 'display_name': 'Recorded synthetic arm', 'kind': 'recorded',
            'model': model, **extra}


def support(root: Path) -> Path:
    root.mkdir(parents=True)
    excerpt = 'The synthetic cohort retained alpha.'
    original = f'{excerpt} A second sentence follows.'
    case = {'schema_version': 1, 'pair_id': 'example-pair', 'paper_key': 'example-paper',
            'criterion': 'Does the excerpt state the cohort retained alpha?',
            'source_sha256': sha(original.encode()), 'span_start': 0, 'span_end': len(excerpt),
            'excerpt': excerpt, 'excerpt_sha256': sha(excerpt.encode())}
    case_sha = digest(case)
    sources = [{'paper_key': 'development-paper', 'family': 'development-family', 'split': 'development',
                'origin': 'original', 'text': 'Different development text.'},
               {'paper_key': 'example-paper', 'family': 'holdout-family', 'split': 'holdout',
                'origin': 'original', 'text': original}]
    terminal = pin(root / 'recorded.bin', wire('supports', SUPPORT_OPTIONS, 'example-model'))
    bundle = {'sources': sources, 'cases': [case], 'synthetic': True,
              'gold': [{'identity': gold_identity(SupportCase(**case), original), 'label': 'supports',
                        'human_provenance': NOTE}],
              'recordings': [{'arm_id': 'recorded', 'case_sha256': case_sha, 'terminal': terminal}],
              'split_frozen_at': '2026-01-01T00:00:00Z', 'holdout_opened_at': '2026-01-03T00:00:00Z'}
    baselines = []
    for kind, field, value in (('verifier', 'verdict', 'reject'), ('minicheck', 'score', 0.75)):
        artifact = {'kind': kind, 'model': f'example-{kind}', 'owner_sha256': OWNERS[kind],
                    'source_reference': 'public synthetic example; not a production baseline run',
                    'projection': 'criterion-original-excerpt', 'critical_met_grounded': True,
                    'rows': [{'case_sha256': case_sha, field: value}]}
        baselines.append({'arm_id': kind, 'display_name': kind, 'kind': kind, 'model': artifact['model'],
                          'threshold': 0.5 if kind == 'minicheck' else None,
                          'artifact': pin(root / f'{kind}.json', artifact)})
    config = {'project_root': str(root / 'selected'), 'attempt': 'example-attempt',
              'inputs': pin(root / 'inputs.json', bundle),
              'arms': [recorded_arm('example-model', runtime_sha256='a' * 64, checkpoint_sha256='b' * 64)],
              'baselines': baselines, 'reference': 'verifier', 'generator_handle': 'example-generator',
              'grader_handle': 'human-only', 'public_input_consent': True,
              'external_guard': 'recorded replay only; no network or model',
              'calibration': {'split_sha256': digest([(s['paper_key'], s['family'], s['split']) for s in sources]),
                              'development_families': ['development-family'],
                              'method': 'fixed synthetic threshold', 'frozen_at': '2026-01-02T00:00:00Z'}}
    pin(root / 'config.json', config)
    return root / 'config.json'


def priority(root: Path) -> Path:
    root.mkdir(parents=True)
    papers, labels, recordings = [], [], []
    for index in range(12):
        paper = {'paper_id': f'paper-{index}', 'family': f'family-{index}',
                 'canonical_ids': [f'doi:10.1234/example-{index}'], 'title': f'Synthetic paper {index}',
                 'abstract': f'Synthetic abstract {index}.', 'split': 'holdout',
                 'stratum': ('shown', 'borderline', 'gate_rejected')[index % 3],
                 'observed_at': '2026-02-01T00:00:00Z'}
        key = digest([normalized(paper['title']), normalized(paper['abstract'])])
        papers.append(paper)
        labels.append({'identity': key, 'label': PRIORITIES[index % 4], 'human_provenance': NOTE})
        terminal = pin(root / f'wire-{index}.bin', wire(PRIORITIES[(index + 1) % 4], PRIORITIES, 'example-model'))
        recordings.append({'arm_id': 'recorded', 'case_sha256': key, 'terminal': terminal})
    gold = {'labels': labels, 'synthetic': True, **STAMP}
    config = {'project_root': str(root / 'selected'), 'attempt': 'example-attempt',
              'inputs': pin(root / 'inputs.json', {'papers': papers, 'recordings': recordings}),
              'gold': pin(root / 'gold.json', gold), 'goals': 'Public example research goals.',
              'arms': [recorded_arm('example-model')],
              'baselines': [{'arm_id': kind, 'display_name': kind, 'kind': kind, 'model': f'example-{kind}',
                             'artifact': None} for kind in ('ml', 'llm')],
              'reference': 'ml', 'generator_handle': 'example-generator', 'grader_handle': 'frozen-human-labels',
              'external_guard': 'recorded replay only; no network or model', 'public_input_consent': True,
              'bootstrap_samples': 200}
    pin(root / 'config.json', config)
    return root / 'config.json'


def collections(root: Path) -> Path:
    root.mkdir(parents=True)
    papers = [{'paper_id': str(i), 'family': f'family-{i}', 'canonical_ids': [f'doi:{i}'],
               'title': f'Paper {i}', 'abstract': f'Original abstract {i}', 'split': 'holdout',
               'stratum': 'public-example', 'observed_at': '2026-09-10T00:00:00Z'} for i in range(3)]
    tree = {'nodes': [
        {'key': 'A', 'name': 'Topic', 'parent': None, 'role': 'topical', 'description': 'Topic research'},
        {'key': 'B', 'name': 'Project', 'parent': 'A', 'role': 'project', 'description': 'Specific project'},
        {'key': 'W', 'name': 'Inbox', 'parent': None, 'role': 'workflow', 'description': 'Workflow only'}],
        'frozen_at': '2026-08-01T00:00:00Z', 'description_sources': [], 'examples': []}
    memberships = (('A', 'B'), (), ('B',))
    labels, recordings = [], []
    for paper, keys in zip(papers, memberships):
        identity = digest([normalized(paper['title']), normalized(paper['abstract'])])
        labels.append({'identity': identity, 'memberships': list(keys), 'human_provenance': NOTE})
        for node in ('A', 'B'):
            unit = digest([identity, node])
            terminal = pin(root / f'{unit}.bin', wire('belongs' if node in keys else 'does_not_belong',
                                                      MEMBERSHIP, 'example-model'))
            recordings.append({'arm_id': 'recorded', 'case_sha256': unit, 'terminal': terminal})
    truth = {'labels': labels, 'synthetic': True, 'split_frozen_at': '2026-09-01T00:00:00Z',
             'holdout_opened_at': '2026-09-11T00:00:00Z'}
    config = {'project_root': str(root / 'selected'), 'attempt': 'example-attempt',
              'inputs': pin(root / 'inputs.json', {'papers': papers, 'tree': tree, 'recordings': recordings}),
              'gold': pin(root / 'gold.json', truth), 'arms': [recorded_arm('example-model')],
              'baselines': [{'arm_id': 'similarity', 'display_name': 'Existing similarity',
                             'kind': 'similarity', 'model': 'example-embedding', 'artifact': None}],
              'reference': 'similarity', 'generator_handle': 'example-generator',
              'grader_handle': 'deterministic-set-v1'}
    pin(root / 'config.json', config)
    return root / 'config.json'


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ('-h', '--help'):
        print(__doc__)
        return 2
    out = Path(args[0]).resolve()
    if out.exists() and any(out.iterdir()):
        print(f'error: {out} exists and is not empty; choose a new directory', file=sys.stderr)
        return 2
    from .faithbench import faithbench
    configs = {name: build(out / name) for name, build in
               (('support', support), ('priority', priority), ('collections', collections))}
    recipe = faithbench(out / 'faithbench')
    print(f'Synthetic examples written to {out}\n')
    for name, path in configs.items():
        print(f'python -m tools.method_validation {name} --config {path}          # admission preview, 0 calls/writes')
        print(f'python -m tools.method_validation {name} --config {path} --run   # recorded replay; writes only under {path.parent}/selected')
        print(f'python -m tools.method_validation {name} --inspect <"Private artifacts:" directory printed by --run>\n')
    print(f'ZOTERO_SUMMARIZER_HOME={out}/faithbench/home zotero-summarizer faithbench interpretation --recipe {recipe}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
