"""One statistics projection for private JSON and readable no-go reports."""
from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil
from random import Random
from statistics import mean, median

from .support import SUPPORT_OPTIONS


def divide(n, d):
    return n / d if d else None


def distribution(values):
    return {'n': len(values), 'mean': mean(values) if values else None,
            'median': median(values) if values else None,
            'p95': sorted(values)[ceil(0.95 * len(values)) - 1] if values else None}


def class_metrics(gold, predicted, labels):
    result = {}
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(gold, predicted))
        fp = sum(g != label and p == label for g, p in zip(gold, predicted))
        fn = sum(g == label and p != label for g, p in zip(gold, predicted))
        result[str(label)] = {'support': gold.count(label), 'tp': tp, 'fp': fp, 'fn': fn,
                             'precision': divide(tp, tp + fp), 'recall': divide(tp, tp + fn),
                             'f1': divide(2 * tp, 2 * tp + fp + fn)}
    return result


def summarize(rows):
    decided = [r for r in rows if r['status'] == 'decision' and r['gold'] is not None]
    gold = [r['gold'] == 'supports' for r in decided]
    pred = [r['support'] for r in decided]
    negatives, positives = gold.count(False), gold.count(True)
    fp = sum(not g and p for g, p in zip(gold, pred))
    fn = sum(g and not p for g, p in zip(gold, pred))
    native = bool(rows) and all(r['capability'] == 'native_three_way' for r in rows)
    brier, calibration = [], defaultdict(list)
    for row in decided:
        readout = row['readout']
        if readout:
            vector = readout['probabilities']
            brier.append(sum((p - int(row['gold'] == label)) ** 2
                             for p, label in zip(vector, SUPPORT_OPTIONS)))
            confidence = vector[SUPPORT_OPTIONS.index(readout['label'])]
            calibration[min(int(confidence * 10), 9)].append((confidence, readout['label'] == row['gold']))
    evaluated = [r for r in rows if r['status'] != 'blocked']
    operational = [r for r in evaluated if r['status'] not in ('decision', 'threshold_abstention')]
    scalar_brier = [(r['baseline_native']['score'] - int(r['gold'] == 'supports')) ** 2
                    for r in rows if r['gold'] is not None and r['baseline_native']
                    and r['baseline_native'].get('score') is not None]
    usage = [r['readout']['usage']['input_tokens'] for r in rows
             if r['readout'] and 'usage' in r['readout']]
    return {'total': len(rows), 'evaluated_pairs': len(evaluated), 'blocked_pairs': len(rows) - len(evaluated),
            'labelled_decisions': len(decided),
            'coverage': divide(sum(r['status'] == 'decision' for r in rows), len(evaluated)),
            'statuses': dict(Counter(r['status'] for r in rows)),
            'operational_error': divide(len(operational), len(evaluated)),
            'binary_score_brier': distribution(scalar_brier) if scalar_brier else None,
            'known_input_tokens': distribution(usage), 'usage_complete': len(usage) == len(evaluated) > 0,
            'known_dispatched_attempts': sum(r['telemetry'].get('dispatched', False) for r in rows),
            'false_confirmation': {'n': fp, 'denominator': negatives, 'rate': divide(fp, negatives)},
            'false_rejection': {'n': fn, 'denominator': positives, 'rate': divide(fn, positives)},
            'binary_error': distribution([int(g != p) for g, p in zip(gold, pred)]),
            'per_binary_class': class_metrics(gold, pred, (True, False)),
            'per_class': class_metrics([r['gold'] for r in decided],
                [r['readout']['label'] for r in decided], SUPPORT_OPTIONS) if native else None,
            'three_way_brier': distribution(brier) if native else None,
            'calibration': [{'bin': k, 'n': len(v), 'mean_probability': mean(x[0] for x in v),
                             'accuracy': mean(x[1] for x in v)} for k, v in sorted(calibration.items())]
                           if native else None,
            'latency_seconds': distribution([r['telemetry']['elapsed_seconds'] for r in rows
                if r['telemetry'].get('elapsed_seconds') is not None]),
            'warm_latency_seconds': None, 'cold_seconds': None, 'peak_memory_bytes': None, 'cost': None,
            'physical_retries': None, 'forward_passes': None,
            'commensurability': 'binary support only; rejection is not contradiction; '
                               'native confidences are not cross-model calibrated'}


def paired(rows, candidate, reference, cfg):
    def admitted(arm):
        return {r['case_sha256']: r for r in rows if r['arm_id'] == arm
                and r['gold'] is not None and r['status'] == 'decision'}
    left, right = admitted(candidate), admitted(reference)
    groups = defaultdict(list)
    disagreements = []
    for key in sorted(left.keys() & right.keys()):
        a, b = left[key], right[key]
        target = a['gold'] == 'supports'
        groups[a['family']].append(int(a['support'] != target) - int(b['support'] != target))
        if a['support'] != b['support']:
            disagreements.append(key)
    units = [mean(values) for values in groups.values()]
    count = sum(len(values) for values in groups.values())
    ci = None
    if len(units) >= cfg.min_papers and count >= cfg.min_pairs:
        rng = Random(cfg.seed)
        samples = sorted(mean(rng.choices(units, k=len(units))) for _ in range(cfg.bootstrap_samples))
        ci = [samples[int(0.025 * len(samples))], samples[ceil(0.975 * len(samples)) - 1]]
    return {'matched_pairs': count, 'independent_paper_families': len(units),
            'matched_coverage': divide(count, sum(r['arm_id'] == candidate for r in rows)),
            'paper_equal_binary_loss_difference': mean(units) if units else None,
            'percentile_95_ci': ci, 'disagreements_for_human_review': disagreements,
            'estimand': 'equal-family mean binary support loss(candidate)-loss(reference), '
                        'paired within family; percentile resampling families, not F1/ratio CI'}


def report(rows, cfg, bundle):
    holdout = [r for r in rows if r['split'] == 'holdout']
    stats = {a.arm_id: summarize([r for r in holdout if r['arm_id'] == a.arm_id])
             for a in (*cfg.arms, *cfg.baselines)}
    for arm in (*cfg.arms, *cfg.baselines):
        stats[arm.arm_id]['configuration'] = arm.model_dump(mode='json')
    pairs = {a.arm_id: paired(holdout, a.arm_id, cfg.reference, cfg) for a in cfg.arms}
    reasons = ['No live-baseline or human holdout/resource qualification study is established.',
               'Cold latency, server peak memory, cost and physical passes/retries are unmeasured.']
    if bundle.synthetic or any(g.human_provenance is None for g in bundle.gold):
        reasons.append('Synthetic or missing human provenance cannot establish a winner.')
    holdout_cases = {r['case_sha256'] for r in holdout if r['gold'] is not None}
    families = {r['family'] for r in holdout if r['gold'] is not None}
    if len(holdout_cases) < cfg.min_pairs or len(families) < cfg.min_papers:
        reasons.append('Below predeclared human-pair/independent-paper floors.')
    gates = {}
    for arm_id, values in stats.items():
        checks = {'coverage': (values['coverage'], cfg.min_coverage, 'min'),
                  'false_confirmation': (values['false_confirmation']['rate'], cfg.max_false_confirmation, 'max'),
                  'false_rejection': (values['false_rejection']['rate'], cfg.max_false_rejection, 'max'),
                  'operational_error': (values['operational_error'], cfg.max_operational_error, 'max'),
                  'request_latency': (values['latency_seconds']['p95'], cfg.max_latency_seconds, 'max'),
                  'warm_latency': (None, cfg.max_latency_seconds, 'max'),
                  'cost': (None, cfg.max_cost, 'max'), 'memory': (None, cfg.max_peak_memory_bytes, 'max')}
        gates[arm_id] = {name: None if v is None else (v >= bound if direction == 'min' else v <= bound)
                        for name, (v, bound, direction) in checks.items()}
        if arm_id in pairs:
            p = pairs[arm_id]
            gates[arm_id]['paired_noninferiority'] = (p['percentile_95_ci'][1] <= cfg.noninferiority_margin
                if p['percentile_95_ci'] else None)
            gates[arm_id]['matched_coverage'] = (p['matched_coverage'] >= cfg.min_coverage
                if p['matched_coverage'] is not None and values['evaluated_pairs'] else None)
    failed = any(value is False for gate in gates.values() for value in gate.values())
    return {'conclusion': 'no-go' if failed else 'inconclusive', 'action': 'retain_baseline',
            'winner': None, 'reasons': reasons, 'reference': cfg.reference,
            'holdout_labelled_pairs': len(holdout_cases), 'paper_families': len(families),
            'arms': stats, 'paired': pairs, 'gates': gates,
            'limitations': 'Offline wiring/comparison, not semantic quality validation or promotion.'}


def readable(result):
    lines = [f"# Support comparison: {result['conclusion']}", 'Action: retain baseline; winner: none.', '']
    lines.extend(f'- {reason}' for reason in result['reasons'])
    for name, values in result['arms'].items():
        lines.extend(['', f'## {name}', f"Coverage: {values['coverage']}; statuses: {values['statuses']}",
                      f"False confirmations: {values['false_confirmation']}",
                      f"False rejections: {values['false_rejection']}",
                      f"Binary error mean/median: {values['binary_error']}",
                      f"Latency mean/median/p95: {values['latency_seconds']}",
                      'Full per-class, capability, paired uncertainty and gates: report.json.'])
    return '\n'.join(lines) + '\n'
