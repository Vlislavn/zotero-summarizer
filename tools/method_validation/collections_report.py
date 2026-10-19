"""Collection-set and independent-question statistics with explicit missing denominators."""
from collections import Counter, defaultdict
from math import ceil
from random import Random
from statistics import mean

from .collections import LABELS
from .support_report import class_metrics, distribution, divide


def paper_outcomes(rows, questions, eligible):
    groups = defaultdict(list)
    for row in rows:
        groups[row['paper_sha256']].append(row)
    result = {}
    for key, group in groups.items():
        first = group[0]
        native = first['capability'] == 'native_membership'
        complete = (len(group) == len(questions) and all(r['status'] == 'decision'
            and r['label'] != 'insufficient_evidence' for r in group)) if native else first['status'] == 'decision'
        selected = ([r['node_key'] for r in group if r['label'] == 'belongs']
                    if native else first['selected_keys']) if complete else None
        ranked = sorted(group, key=lambda r: (-r['readout']['probabilities'][0], r['node_key'])) if native and complete else []
        ranked_keys = [r['node_key'] for r in ranked] if native else (selected or [])
        result[key] = {'family': first['family'], 'stratum': first['stratum'],
            'gold': first['gold_memberships'], 'selected': selected, 'ranked': ranked_keys,
            'complete': complete, 'blocked': all(r['status'] == 'blocked' for r in group),
            'confirmed_no_match': bool(native and complete and not selected and set(questions) == eligible),
            'retrieval_empty': bool(not native and complete and not selected)}
    return result


def set_metrics(papers, eligible):
    judged = [p for p in papers.values() if p['complete'] and p['gold'] is not None]
    metrics = {}
    for key in sorted(eligible):
        pairs = [(key in p['gold'], key in p['selected']) for p in judged]
        metrics[key] = class_metrics([g for g, _ in pairs], [p for _, p in pairs], (True,))['True']
    tp, fp, fn = (sum(m[k] for m in metrics.values()) for k in ('tp', 'fp', 'fn'))
    losses = [len(set(p['gold']) ^ set(p['selected'])) / len(eligible) for p in judged]
    no_match = [p for p in judged if not p['gold']]
    depth = min(3, len(eligible))
    precision = [len(set(p['ranked'][:depth]) & set(p['gold'])) / depth for p in judged]
    recall = [len(set(p['ranked'][:depth]) & set(p['gold'])) / len(p['gold']) for p in judged if p['gold']]
    return {'labelled_complete_papers': len(judged), 'per_collection': metrics,
            'micro_f1': divide(2 * tp, 2 * tp + fp + fn),
            'macro_f1': mean([v['f1'] for v in metrics.values() if v['f1'] is not None])
                if any(v['f1'] is not None for v in metrics.values()) else None,
            'exact_set_accuracy': divide(sum(set(p['gold']) == set(p['selected']) for p in judged), len(judged)),
            'set_hamming_loss': distribution(losses),
            'ranking_depth': depth, 'precision_at3': distribution(precision), 'recall_at3': distribution(recall),
            'false_assignment_on_no_match': {'n': sum(bool(p['selected']) for p in no_match),
                'denominator': len(no_match), 'rate': divide(sum(bool(p['selected']) for p in no_match), len(no_match))}}


def summary(rows, questions, eligible, timings=()):
    papers = paper_outcomes(rows, questions, eligible)
    native = [r for r in rows if r['capability'] == 'native_membership']
    evaluated = [r for r in native if r['status'] != 'blocked']
    decided = [r for r in evaluated if r['status'] == 'decision' and r['label'] != 'insufficient_evidence']
    brier, calibration = [], defaultdict(list)
    for r in native:
        if r['status'] != 'decision' or r['gold_memberships'] is None:
            continue
        gold = 'belongs' if r['node_key'] in r['gold_memberships'] else 'does_not_belong'
        vector = r['readout']['probabilities']
        brier.append(sum((p - int(label == gold)) ** 2 for p, label in zip(vector, LABELS)))
        probability = vector[LABELS.index(r['label'])]
        calibration[min(int(probability * 10), 9)].append((probability, r['label'] == gold))
    bins = [{'bin': k, 'n': len(v), 'probability': mean(x[0] for x in v), 'accuracy': mean(x[1] for x in v)}
            for k, v in sorted(calibration.items())]
    exposed = [p for p in papers.values() if not p['blocked']]
    return {**set_metrics(papers, eligible), 'total_papers': len(papers),
            'complete_papers': sum(p['complete'] for p in papers.values()),
            'paper_coverage': divide(sum(p['complete'] for p in exposed), len(exposed)),
            'question_coverage': divide(len(decided), len(evaluated)),
            'question_statuses': dict(Counter(r['status'] for r in rows)),
            'choices': dict(Counter(r['label'] for r in native if r['label'] is not None)),
            'confirmed_native_no_match': sum(p['confirmed_no_match'] for p in papers.values()),
            'empty_similarity_retrieval': sum(p['retrieval_empty'] for p in papers.values()),
            'brier': distribution(brier) if native else None, 'calibration': bins or None,
            'ece': divide(sum(b['n'] * abs(b['probability'] - b['accuracy']) for b in bins), len(brier)),
            'complete_paper_latency_seconds': distribution([t['elapsed_seconds'] for t in timings]),
            'paper_latency_scope': 'First question start through final case-row ACK for wholly dispatched paper/arm; '
                                   'common admission/snapshots/startup excluded; unsigned local observation.',
            'cold_seconds': None, 'warm_seconds': None, 'peak_memory_bytes': None, 'cost': None,
            'physical_retries': None, 'forward_passes': None, 'manual_corrections': None,
            'dispatched_attempts': sum(r['telemetry'].get('dispatched', False) for r in rows)}


def paired(rows, candidate, cfg, questions, eligible):
    left = paper_outcomes([r for r in rows if r['arm_id'] == candidate], questions, eligible)
    right = paper_outcomes([r for r in rows if r['arm_id'] == cfg.reference], questions, eligible)
    common = sorted(k for k in left.keys() & right.keys() if left[k]['complete'] and right[k]['complete']
                    and left[k]['gold'] is not None)
    groups, disagreements = defaultdict(list), []
    for key in common:
        a, b = left[key], right[key]
        loss = lambda p: len(set(p['gold']) ^ set(p['selected'])) / len(eligible)
        groups[a['family']].append(loss(a) - loss(b))
        if set(a['selected']) != set(b['selected']):
            disagreements.append(key)
    units = [mean(v) for v in groups.values()]
    ci = None
    if len(common) >= cfg.min_cases and len(units) >= cfg.min_families:
        rng = Random(cfg.seed)
        samples = sorted(mean(rng.choices(units, k=len(units))) for _ in range(cfg.bootstrap_samples))
        ci = [samples[int(0.025 * len(samples))], samples[ceil(0.975 * len(samples)) - 1]]
    return {'matched_papers': len(common), 'families': len(units), 'coverage': divide(len(common), len(left)),
            'equal_family_hamming_loss_difference': mean(units) if units else None, 'percentile_95_ci': ci,
            'candidate': set_metrics({k: left[k] for k in common}, eligible),
            'reference': set_metrics({k: right[k] for k in common}, eligible),
            'disagreements_for_human_review': disagreements,
            'estimand': 'Equal-family mean normalized set-Hamming loss(candidate)-loss(reference); '
                        'paired-family percentile bootstrap on selected common complete cohort; not F1/ratio CI.'}


def report(rows, cfg, inputs, truth, *, timings=()):
    eligible = {n.key for n in inputs.tree.nodes if n.role != 'workflow'}
    questions = tuple(sorted(eligible)) if inputs.tree.selected_keys is None else inputs.tree.selected_keys
    held = [r for r in rows if r['split'] == 'holdout']
    held_keys = {r['paper_sha256'] for r in held}
    arms = {a.arm_id: summary([r for r in held if r['arm_id'] == a.arm_id], questions, eligible,
            [t for t in timings if t['arm_id'] == a.arm_id and t['paper_sha256'] in held_keys])
            for a in (*cfg.arms, *cfg.baselines)}
    workflow_names = {n.name for n in inputs.tree.nodes if n.role == 'workflow'}
    for arm in cfg.baselines:
        arms[arm.arm_id]['workflow_suggestions_excluded'] = sum(
            len(set(r['baseline_native']['readout']['suggested_collections']) & workflow_names)
            for r in held if r['arm_id'] == arm.arm_id and r['status'] == 'decision')
    gates = {}
    for arm, values in arms.items():
        checks = {'coverage': (values['paper_coverage'], cfg.min_coverage, True),
                  'micro_f1': (values['micro_f1'], cfg.min_micro_f1, True),
                  'false_assignment': (values['false_assignment_on_no_match']['rate'], cfg.max_false_assignment, False),
                  'paper_latency': (values['complete_paper_latency_seconds']['p95'], cfg.max_latency_seconds, False),
                  'cold': (None, cfg.max_latency_seconds, False), 'warm': (None, cfg.max_latency_seconds, False),
                  'memory': (None, cfg.max_peak_memory_bytes, False), 'cost': (None, cfg.max_cost, False)}
        gates[arm] = {k: None if v is None else (v >= bound if lower else v <= bound)
                      for k, (v, bound, lower) in checks.items()}
    keys = {r['paper_sha256'] for r in held if r['gold_memberships'] is not None}
    gold = {g.identity: g for g in truth.labels}
    shortlist = [divide(len(set(gold[k].memberships) & set(questions)), len(gold[k].memberships))
                 for k in keys if gold[k].memberships]
    return {'conclusion': 'no-go' if any(v is False for g in gates.values() for v in g.values()) else 'inconclusive',
            'action': 'retain_baseline', 'winner': None, 'reference': cfg.reference, 'synthetic': truth.synthetic,
            'holdout_labelled_papers': len(keys), 'shortlist_recall': distribution(shortlist),
            'selected_question_keys': list(questions), 'eligible_keys': sorted(eligible),
            'arms': arms, 'gates': gates,
            'strata': {a.arm_id: {s: summary([r for r in held if r['arm_id'] == a.arm_id and r['stratum'] == s],
                       questions, eligible, [t for t in timings if t['arm_id'] == a.arm_id and t['paper_sha256']
                       in {r['paper_sha256'] for r in held if r['stratum'] == s}])
                       for s in sorted({p.stratum for p in inputs.papers if p.split == 'holdout'})}
                       for a in (*cfg.arms, *cfg.baselines)},
            'paired': {a.arm_id: paired(held, a.arm_id, cfg, questions, eligible) for a in cfg.arms},
            'taxonomy': {n.key: {'name': n.name, 'role': n.role, 'nested': n.parent is not None,
                         'duplicate_name': sum(x.name == n.name for x in inputs.tree.nodes) > 1} for n in inputs.tree.nodes},
            'limitations': ['Frozen offline comparator, not full#42, real-model quality, native-user writes or a winner.',
                'Approximately100human-confirmed papers/20families and rare/nested/duplicate/no-match controls remain required.',
                'Cold/warm/resource/cost/pass/manual-correction evidence is unavailable, never zero.',
                'Similarity ranks at most3 original names, not scores/vectors or confident no-match.',
                'Threshold coverage and successful common cohorts are selected; model confidences not interchangeable.']}


def readable(result):
    lines = [f"# Collection comparison: {result['conclusion']}", 'Action: retain baseline; winner: none.', '',
             *['- ' + s for s in result['limitations']]]
    for arm, value in result['arms'].items():
        lines += ['', '## ' + arm, f"Complete-paper/question coverage: {value['paper_coverage']} / {value['question_coverage']}",
                  f"Micro/macroF1 / exact set: {value['micro_f1']} / {value['macro_f1']} / {value['exact_set_accuracy']}",
                  f"Hamming error mean/median/p95: {value['set_hamming_loss']}",
                  'Full statuses/per-collection/strata/calibration/paired/resource denominators: report.json.']
    return '\n'.join(lines) + '\n'
