"""Public dispatch admission, human-paired eligibility and cumulative input bounds."""
from pathlib import Path

import httpx
import pytest

from tools.method_validation import support_run
from tools.method_validation.decision_adapter import NativeDecisionAdapter, endpoint, evaluate_support, payload
from tools.method_validation.support import FrozenSupportInput, SupportCase, digest, encoded, gold_identity, sha
from tools.method_validation.support_report import report
from test_support_fixtures import fixture, native_wire, validated
from test_support_metrics import row


@pytest.mark.parametrize('entry', ['adapter', 'evaluate'])
@pytest.mark.parametrize('field,value', [('public_input_consent', False), ('external_guard', None)])
def test_public_native_dispatch_requires_consent_and_guard(tmp_path, entry, field, value):
    cfg, _, case, _ = fixture(tmp_path, kind='native')
    cfg[field] = value
    config = validated(cfg)
    calls, retained = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=encoded(native_wire()))
    with pytest.raises(ValueError, match='consent|guard'):
        if entry == 'adapter':
            NativeDecisionAdapter(config.arms[0], config, transport=httpx.MockTransport(handler))(
                payload(SupportCase(**case), config), retained.append)
        else:
            evaluate_support(SupportCase(**case), config.arms[0], config, retained.append,
                             transport=httpx.MockTransport(handler))
    assert calls == retained == []


@pytest.mark.parametrize('kind', ['blocked', 'native'])
def test_recorded_evaluation_rejects_blocked_or_wrong_mode_before_retaining(tmp_path, kind):
    cfg, _, case, _ = fixture(tmp_path, kind=kind)
    config = validated(cfg)
    retained = []
    with pytest.raises(ValueError, match='recorded|blocked'):
        evaluate_support(SupportCase(**case), config.arms[0], config, retained.append,
                         recorded=encoded(native_wire()))
    assert retained == []


def test_direct_native_adapter_reserves_declared_work_before_dispatch(tmp_path):
    cfg, _, _, _ = fixture(tmp_path, kind='native')
    cfg['max_work_units'] = 1
    config = validated(cfg)
    with pytest.raises(ValueError, match='budget'):
        NativeDecisionAdapter(config.arms[0], config, transport=object())


@pytest.mark.parametrize('url', ['http://native.invalid/v1/systemone',
                               'http://192.0.2.1/v1/systemone'])
def test_remote_plaintext_endpoint_is_not_admitted(url):
    with pytest.raises(ValueError, match='HTTPS|loopback'):
        endpoint(url)


@pytest.mark.parametrize('host', ['127.0.0.1', '127.0.0.2', '[::1]', 'localhost'])
def test_explicit_loopback_http_endpoint_remains_available(host):
    endpoint(f'http://{host}:18081/v1/systemone')


@pytest.mark.parametrize('human_count', [0, 60, 80])
def test_paired_estimates_require_provenanced_holdout_labels(tmp_path, human_count):
    cfg, bundle, case, _ = fixture(tmp_path)
    bundle.update(sources=[], cases=[], gold=[], recordings=[], synthetic=False)
    rows = []
    for index in range(80):
        paper = index // 4
        original = f'Original source for independent paper {paper}.'
        if index % 4 == 0:
            bundle['sources'].append({'paper_key': str(paper), 'family': str(paper),
                'split': 'holdout', 'origin': 'original', 'text': original})
        current = {**case, 'pair_id': str(index), 'paper_key': str(paper),
                   'criterion': f'Criterion {index}', 'source_sha256': sha(original.encode()),
                   'span_start': 0, 'span_end': len(original), 'excerpt': original,
                   'excerpt_sha256': sha(original.encode())}
        bundle['cases'].append(current)
        bundle['gold'].append({'identity': gold_identity(SupportCase(**current), original),
            'label': 'supports', 'human_provenance': 'named synthetic test reviewer' if index < human_count else None})
        for arm in ('native', 'verifier', 'minicheck'):
            rows.append(row(digest(current), 'supports', 'supports', [0.8, 0.1, 0.1],
                            arm=arm, family=str(paper)))
    result = report(rows, validated(cfg), FrozenSupportInput.model_validate(bundle))
    paired = result['paired']['native']
    assert paired['matched_pairs'] == human_count
    assert paired['matched_coverage'] == human_count / 80
    assert (paired['percentile_95_ci'] is not None) == (human_count == 80)
    assert result['gates']['native']['paired_noninferiority'] is (True if human_count == 80 else None)
    assert result['holdout_labelled_pairs'] == 80  # Descriptive labels remain visible, not discarded.
    assert result['winner'] is None


def test_frozen_inputs_share_one_aggregate_byte_budget_before_owner_creation(tmp_path):
    cfg, bundle, _, _ = fixture(tmp_path)
    refs = [cfg['inputs'], *(b['artifact'] for b in cfg['baselines']),
            *(r['terminal'] for r in bundle['recordings'])]
    sizes = [Path(ref['path']).stat().st_size for ref in refs]
    cfg['max_input_bytes'] = max(sizes)
    assert all(size <= cfg['max_input_bytes'] for size in sizes)
    assert sum(sizes) > cfg['max_input_bytes']
    with pytest.raises(ValueError, match='byte|budget'):
        support_run.run(validated(cfg), encoded(cfg))
    assert not (tmp_path / 'selected').exists()
