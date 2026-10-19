"""One priority-statistics projection; missing decisions never become negatives."""
from collections import Counter, defaultdict
from math import ceil, log2
from random import Random
from statistics import mean

from .priority import LABELS, ORDINAL
from .support_report import class_metrics, distribution, divide


def rank_score(row):
    if row['readout']:
        return sum(p * ORDINAL[label] for p, label in zip(row['readout']['probabilities'], LABELS))
    if row['capability'] == 'ml_regression':
        return row['baseline_native'].get('score')
    return ORDINAL[row['label']]


def ranking(rows, depth):
    missing = any(rank_score(row) is None for row in rows)
    if len(rows) < depth or missing:
        return {'depth': depth, 'available': len(rows), 'precision': None, 'ndcg': None,
                'must_read_retention': None,
                'reason': 'missing regression ranking score' if missing else 'insufficient common ranking depth'}
    ordered = sorted(rows, key=lambda r: (-rank_score(r), r['case_sha256']))[:depth]
    ideal = sorted((ORDINAL[r['gold']] for r in rows), reverse=True)[:depth]
    dcg = sum((2 ** ORDINAL[r['gold']] - 1) / log2(i + 2) for i, r in enumerate(ordered))
    idcg = sum((2 ** value - 1) / log2(i + 2) for i, value in enumerate(ideal))
    return {'depth': depth, 'available': len(rows),
            'precision': sum(r['gold'] in LABELS[:2] for r in ordered) / depth,
            'ndcg': divide(dcg, idcg),
            'must_read_retention': divide(sum(r['gold'] == 'must_read' for r in ordered),
                                         sum(r['gold'] == 'must_read' for r in rows))}


def summary(rows, depth):
    decided = [r for r in rows if r['status'] == 'decision' and r['gold'] is not None]
    gold, predicted = [r['gold'] for r in decided], [r['label'] for r in decided]
    must = [r for r in decided if r['gold'] == 'must_read']
    strong = [r for r in decided if r['label'] in LABELS[:2]]
    brier, bins = [], defaultdict(list)
    for row in decided:
        if row['readout']:
            vector = row['readout']['probabilities']
            brier.append(sum((p - int(row['gold'] == label)) ** 2 for p, label in zip(vector, LABELS)))
            p = vector[LABELS.index(row['label'])]
            bins[min(int(p * 10), 9)].append((p, row['label'] == row['gold']))
    calibration = [{'bin': k, 'n': len(v), 'probability': mean(x[0] for x in v),
                    'accuracy': mean(x[1] for x in v)} for k, v in sorted(bins.items())]
    evaluated = [r for r in rows if r['status'] != 'blocked']
    return {'total': len(rows), 'statuses': dict(Counter(r['status'] for r in rows)),
            'coverage': divide(sum(r['status'] == 'decision' for r in evaluated), len(evaluated)),
            'labelled_decisions': len(decided), 'per_class': class_metrics(gold, predicted, LABELS),
            'ordinal_error': distribution([abs(ORDINAL[r['label']] - ORDINAL[r['gold']]) for r in decided]),
            'must_read_retention': divide(sum(r['label'] in LABELS[:2] for r in must), len(must)),
            'false_strong': divide(sum(r['gold'] == 'dont_read' for r in strong), len(strong)),
            'brier': distribution(brier) if brier else None, 'calibration': calibration or None,
            'ece': divide(sum(v['n'] * abs(v['probability'] - v['accuracy']) for v in calibration), len(brier)),
            'ranking': ranking(decided, depth),
            'latency_seconds': distribution([r['telemetry']['elapsed_seconds'] for r in rows
                if r['telemetry'].get('elapsed_seconds') is not None]),
            'cold_latency_seconds': None, 'warm_latency_seconds': None, 'peak_memory_bytes': None,
            'cost': None, 'physical_retries': None, 'forward_passes': None,
            'dispatched_attempts': sum(r['telemetry'].get('dispatched', False) for r in rows)}


def paired(rows, candidate, reference, cfg):
    def accepted(arm):
        return {r['case_sha256']: r for r in rows if r['arm_id'] == arm
                and r['status'] == 'decision' and r['gold'] is not None}
    left, right = accepted(candidate), accepted(reference)
    common = sorted(left.keys() & right.keys())
    groups, disagreements = defaultdict(list), []
    for key in common:
        a, b = left[key], right[key]
        groups[a['family']].append(abs(ORDINAL[a['label']] - ORDINAL[a['gold']])
                                  - abs(ORDINAL[b['label']] - ORDINAL[b['gold']]))
        if a['label'] != b['label']:
            disagreements.append(key)
    units = [mean(v) for v in groups.values()]
    ci = None
    if len(common) >= cfg.min_cases and len(units) >= cfg.min_families:
        rng = Random(cfg.seed)
        samples = sorted(mean(rng.choices(units, k=len(units))) for _ in range(cfg.bootstrap_samples))
        ci = [samples[int(0.025 * len(samples))], samples[ceil(0.975 * len(samples)) - 1]]
    return {'matched_cases': len(common), 'families': len(units),
            'coverage': divide(len(common), sum(r['arm_id'] == candidate for r in rows)),
            'candidate_ranking': ranking([left[k] for k in common], cfg.ranking_depth),
            'reference_ranking': ranking([right[k] for k in common], cfg.ranking_depth),
            'equal_family_ordinal_loss_difference': mean(units) if units else None,
            'percentile_95_ci': ci, 'disagreements_for_human_review': disagreements,
            'estimand': 'equal-family mean absolute ordinal loss(candidate)-loss(reference); '
                        'paired family resampling, not a CI for ranking ratios; common successful cohort is selected'}


def report(rows, cfg, inputs, truth):
    held = [r for r in rows if r['split'] == 'holdout']
    arms = {a.arm_id: summary([r for r in held if r['arm_id'] == a.arm_id], cfg.ranking_depth)
            for a in (*cfg.arms, *cfg.baselines)}
    strata = {a.arm_id: {s: summary([r for r in held if r['arm_id'] == a.arm_id and r['stratum'] == s],
                                  cfg.ranking_depth) for s in ('shown', 'borderline', 'gate_rejected')}
              for a in (*cfg.arms, *cfg.baselines)}
    gates = {}
    for arm, values in arms.items():
        checks = {'coverage': (values['coverage'], cfg.min_coverage, True),
                  'must_read_retention': (values['must_read_retention'], cfg.min_must_read_retention, True),
                  'false_strong': (values['false_strong'], cfg.max_false_strong, False),
                  'ordinal_error': (values['ordinal_error']['mean'], cfg.max_ordinal_error, False),
                  'request_latency': (values['latency_seconds']['p95'], cfg.max_latency_seconds, False),
                  'cold_latency': (None, cfg.max_latency_seconds, False),
                  'warm_latency': (None, cfg.max_latency_seconds, False),
                  'memory': (None, cfg.max_peak_memory_bytes, False), 'cost': (None, cfg.max_cost, False)}
        gates[arm] = {k: None if v is None else (v >= bound if lower else v <= bound)
                      for k, (v, bound, lower) in checks.items()}
    failed = any(v is False for values in gates.values() for v in values.values())
    labelled = {r['case_sha256'] for r in held if r['gold'] is not None}
    families = {r['family'] for r in held if r['gold'] is not None}
    reasons = ['Native cold/warm memory/cost/physical-pass measurements and controlled live baselines are unavailable.',
               'No model is promoted; provider confidence is not cross-model calibrated.']
    if (truth.synthetic or any(g.human_provenance is None for g in truth.labels)
            or len(labelled) < cfg.min_cases or len(families) < cfg.min_families):
        reasons.append('Synthetic/unverified human labels or below the predeclared real-label floor.')
    return {'conclusion': 'no-go' if failed else 'inconclusive', 'action': 'retain_baseline', 'winner': None,
            'reference': cfg.reference, 'reasons': reasons, 'ordinal_mapping': ORDINAL,
            'holdout_labelled_cases': len(labelled), 'holdout_labelled_families': len(families),
            'split_frozen_at': truth.split_frozen_at.isoformat(),
            'holdout_opened_at': truth.holdout_opened_at.isoformat(),
            'input_gold_class_counts': dict(Counter(g.label for g in truth.labels)),
            'input_sampling_counts': dict(Counter(p.stratum for p in inputs.papers)),
            'holdout_gold_class_counts': dict(Counter(r['gold'] for r in held
                if r['arm_id'] == cfg.reference and r['gold'] is not None)),
            'holdout_sampling_counts': dict(Counter(p.stratum for p in inputs.papers if p.split == 'holdout')),
            'arms': arms, 'strata': strata,
            'paired': {a.arm_id: paired(held, a.arm_id, cfg.reference, cfg) for a in cfg.arms}, 'gates': gates,
            'limitations': 'Frozen offline comparison, not deployed ranking, a complete empirical study or scientific validation.'}


def readable(result):
    lines = [f"# Reading-priority comparison: {result['conclusion']}",
             'Action: retain baseline; winner: none.', '', *['- ' + x for x in result['reasons']]]
    for key, values in result['arms'].items():
        lines.extend(['', '## ' + key, f"Coverage/status: {values['coverage']} / {values['statuses']}",
                      f"Ordinal error mean/median: {values['ordinal_error']}",
                      f"Must-read retention / false strong: {values['must_read_retention']} / {values['false_strong']}",
                      f"Latency mean/median/p95: {values['latency_seconds']}",
                      'Complete class/stratum/ranking/calibration/paired/resource denominators: report.json.'])
    return '\n'.join(lines) + '\n'
