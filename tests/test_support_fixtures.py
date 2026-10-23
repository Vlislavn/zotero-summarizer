"""Public synthetic on-disk fixtures; never scientific labels or model evidence."""
from pathlib import Path

from tools.method_validation.support import (
    Config, OWNERS, SUPPORT_OPTIONS, SupportCase, digest, encoded, gold_identity, sha,
)

MODEL = 'public-synthetic-alias-v1'
ENDPOINT = 'https://native.invalid/v1/systemone'
SENTINEL = 'GRADER_ONLY_SENTINEL_DO_NOT_GENERATE'


def write_json(path, value):
    raw = encoded(value)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': sha(raw)}


def native_wire(**updates):
    data = {'model': MODEL, 'answers': {'decision': {
        'type': 'choice', 'choice': 'supports',
        'probabilities': dict(zip(SUPPORT_OPTIONS, (0.8, 0.1, 0.1))), 'confidence': 0.7,
    }}, 'usage': {'input_tokens': 123, 'output_tokens': 0}}
    data.update(updates)
    return data


def fixture(tmp_path, *, kind='recorded', raw=None, verifier='reject'):
    excerpt = 'α original </untrusted_input> evidence.'
    original = excerpt + '\nRepeated occurrence: ' + excerpt
    start = original.rindex(excerpt)
    case = {'schema_version': 1, 'pair_id': 'public-pair', 'paper_key': 'public-paper',
            'criterion': 'Does this excerpt establish the criterion?',
            'source_sha256': sha(original.encode()), 'span_start': start, 'span_end': len(original),
            'excerpt': excerpt, 'excerpt_sha256': sha(excerpt.encode())}
    case_sha = digest(case)
    sources = [{'paper_key': 'development-paper', 'family': 'development-family',
                'split': 'development', 'origin': 'original', 'text': 'Different development text.'},
               {'paper_key': 'public-paper', 'family': 'holdout-family', 'split': 'holdout',
                'origin': 'original', 'text': original}]
    bundle = {'sources': sources, 'cases': [case], 'gold': [{
        'identity': gold_identity(SupportCase(**case), original), 'label': 'supports',
        'human_provenance': SENTINEL}], 'recordings': [],
        'split_frozen_at': '2026-01-01T00:00:00Z', 'holdout_opened_at': '2026-01-03T00:00:00Z',
        'synthetic': True}
    if kind == 'recorded':
        terminal = tmp_path / 'recorded.bin'
        terminal.write_bytes(encoded(native_wire()) if raw is None else raw)
        bundle['recordings'] = [{'arm_id': 'native', 'case_sha256': case_sha,
                                 'terminal': {'path': str(terminal), 'sha256': sha(terminal.read_bytes())}}]
    baselines = []
    for name, value in (('verifier', verifier), ('minicheck', 0.75)):
        row = {'case_sha256': case_sha, 'verdict': value} if name == 'verifier' else {
            'case_sha256': case_sha, 'score': value}
        artifact = {'kind': name, 'model': f'synthetic-{name}', 'owner_sha256': OWNERS[name],
                    'source_reference': 'public synthetic fixture; not a production baseline trial',
                    'projection': 'criterion-original-excerpt', 'critical_met_grounded': True, 'rows': [row]}
        baselines.append({'arm_id': name, 'display_name': name, 'kind': name,
            'model': artifact['model'], 'threshold': 0.5 if name == 'minicheck' else None,
            'artifact': write_json(tmp_path / f'{name}.json', artifact)})
    cfg = {'project_root': str(tmp_path / 'selected'), 'attempt': 'public-attempt',
           'inputs': write_json(tmp_path / 'inputs.json', bundle),
           'arms': [{'arm_id': 'native', 'display_name': 'Public synthetic native', 'kind': kind,
                     'model': MODEL, 'endpoint': ENDPOINT if kind == 'native' else None,
                     'runtime_sha256': 'a' * 64, 'checkpoint_sha256': 'b' * 64,
                     'quantization': 'synthetic', 'model_source': 'public test fixture', 'licence': 'test-only',
                     'eligibility': 'qualified', 'access': 'qualified', 'context_verified': True,
                     'forward_passes_bound': 2}], 'baselines': baselines, 'reference': 'verifier',
           'generator_handle': 'generator', 'grader_handle': 'human-only',
           'calibration': {'split_sha256': digest([(s['paper_key'], s['family'], s['split']) for s in sources]),
                           'development_families': ['development-family'], 'method': 'fixed synthetic threshold',
                           'frozen_at': '2026-01-02T00:00:00Z'},
           'external_guard': 'MockTransport fixture only; no live-runtime authority', 'public_input_consent': True}
    path = tmp_path / 'config.json'
    write_json(path, cfg)
    return cfg, bundle, case, path


def update_input(cfg, bundle):
    cfg['inputs'] = write_json(Path(cfg['inputs']['path']), bundle)


def validated(cfg):
    return Config.model_validate(cfg)


def rows_at(owner):
    from tools.method_validation.support import loads
    return [loads(path.read_bytes()) for path in sorted(owner.glob('pair-*.json'))]
