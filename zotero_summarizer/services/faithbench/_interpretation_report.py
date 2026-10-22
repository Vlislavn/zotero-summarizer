"""Blinded human scoring and paper-paired decisions; never grade scientific prose by keywords."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
from statistics import mean, median
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from zotero_summarizer.services.faithbench._interpretation import read_run
from zotero_summarizer.services.faithbench._interpretation_inputs import Hash, project_path, publish_json
from zotero_summarizer.services.faithbench._stats import _percentile
from zotero_summarizer.services.faithbench._corpus import chunk_text
from zotero_summarizer.services.library.interpretation import selected_original
from zotero_summarizer.services.library._digest_audit import canonical_hash


class Judgment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    response_sha256: Hash
    question: str = ''
    correct: bool | None = None
    overclaim: bool | None = None
    add_regression: bool | None = None
    card: dict[str, tuple[bool, bool]] = Field(default_factory=dict)


class Labels(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reviewer: str = Field(min_length=1)
    manifest_sha256: Hash
    judgments: dict[str, Judgment]
    controlled_runtime: bool | None = None
    complete_token_accounting: bool | None = None
    model_identity_verified: bool | None = None
    operational_evidence: dict[str, Hash] = Field(default_factory=dict)


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold()


def blind_packets(records):
    packets = {}
    for record in records:
        assignment, result = record['assignment'], record['review']
        packets[assignment['review_blind_id']] = {
            'question': '', 'response_sha256': canonical_hash(result), 'review': result['digest'] if result else None,
            'source': result['text'] if result else None,
        }
        if result and result['interpretation'].get('claims'):
            card = result['interpretation']
            targets = {normalized(card['claims'][c['index']]) + ' [' + canonical_hash([card['source'], c['evidence']]) + ']':
                       {'claim': card['claims'][c['index']], 'passage_ids': c['evidence']} for c in card['checks']['checks']}
            packets[assignment['card_blind_id']] = {
                'question': '', 'response_sha256': canonical_hash(card), 'card_claims': card['claims'],
                'card_evidence': targets, 'card_checks': card['checks'], 'card_source': card['source'],
                'card_passages': chunk_text(selected_original(result)), 'source': result['text']}
        for row in record['questions']:
            response = row.get('output', {}).get('response')
            packets[assignment['question_blind_ids'][row['item_id']]] = {
                'question': row['question'], 'response_sha256': canonical_hash(response),
                'response': response, 'source': result['text'] if result else None,
            }
    return dict(sorted(packets.items()))


def _labels(path, manifest_sha, packets):
    labels = Labels.model_validate_json(Path(path).read_bytes())
    if labels.manifest_sha256 != manifest_sha or set(labels.judgments) - set(packets):
        raise ValueError('Human labels belong to another response inventory')
    for key, judgment in labels.judgments.items():
        packet = packets[key]
        if judgment.response_sha256 != packet['response_sha256'] or normalized(judgment.question) != normalized(packet['question']):
            raise ValueError('Human response/question identity mismatch')
        expected = set(packet.get('card_evidence', {}))
        if {normalized(claim) for claim in judgment.card} != expected:
            raise ValueError('Human card labels omit scientific leaves')
    for name, expected in labels.operational_evidence.items():
        evidence = (Path(path).parent / name).resolve()
        if not evidence.is_relative_to(Path(path).parent.resolve()) or hashlib.sha256(evidence.read_bytes()).hexdigest() != expected:
            raise ValueError('Operational evidence reference changed')
    if any(value is True for value in (labels.controlled_runtime, labels.complete_token_accounting,
                                      labels.model_identity_verified)) and not labels.operational_evidence:
        raise ValueError('Positive operational judgments require independent referenced evidence')
    return labels


def _resources(record):
    calls = record['calls']
    if not calls or any(not call['completions'] for call in calls):
        return None
    values = [(event['usage'] or {}).get(key) for call in calls for event in call['completions']
              for key in ('prompt_tokens', 'completion_tokens')]
    return sum(values) if all(type(value) is int and value >= 0 for value in values) else None


def _metrics(inputs, records, labels):
    result, losses = {}, {}
    items = {row['item_id']: row for row in inputs['items']}
    for arm in inputs['recipe']['labels']:
        observations, paper_losses, cells, tokens, times = [], {}, {}, {}, []
        review_failures, review_overclaims, card_failures, add_regressions, missing = 0, [], [], [], 0
        for record in (r for r in records if r['assignment']['condition'] == arm):
            assignment = record['assignment']
            review_failures += record['review_status'] == 'failed'
            judgment = labels.judgments.get(assignment['review_blind_id'])
            if record['review'] is not None:
                review_overclaims.append(None if judgment is None else judgment.overclaim)
                add_regressions.append(None if judgment is None else judgment.add_regression)
                claims = record['review']['interpretation'].get('claims', [])
                card_judgment = labels.judgments.get(assignment['card_blind_id'])
                if claims and card_judgment is None:
                    missing += 1
                if card_judgment is not None:
                    card_failures.extend(not correct or not link for correct, link in card_judgment.card.values())
            paper_rows = []
            for row in record['questions']:
                item, judgment = items[row['item_id']], labels.judgments.get(assignment['question_blind_ids'][row['item_id']])
                failed = row['status'] == 'failed'
                abstained = not failed and row['output']['response']['answer'] is None
                overclaim = None if failed else (False if abstained else None if judgment is None else judgment.overclaim)
                correct = False if failed else (abstained if item['kind'] == 'trap' else
                                                False if abstained else None if judgment is None else judgment.correct)
                loss = int(overclaim) if overclaim is not None else (1 if arm == 'C' else 0)
                paper_rows.append(loss)
                observations.append((overclaim, correct, failed, abstained, item['kind']))
                cell = inputs['recipe']['groups'][assignment['paper']]['domain'] + '/' + item['kind'] + '/' + inputs['recipe']['stance'][item['item_id']]
                cells.setdefault(cell, []).append((overclaim, correct))
            paper_losses[assignment['paper']] = mean(paper_rows)
            tokens[assignment['paper']] = _resources(record)
            times.append(record['seconds'])
        assigned, overall_accuracy, failures, abstentions, kinds = map(list, zip(*observations))
        accuracy = [value for value, kind in zip(overall_accuracy, kinds) if kind == 'qa']
        result[arm] = {'assigned': len(assigned), 'overclaim_lower': sum(x is True for x in assigned) / len(assigned),
                       'overclaim_upper': sum(x is not False for x in assigned) / len(assigned),
                       'overclaim_counts': [sum(x is True for x in assigned), sum(x is not False for x in assigned)],
                       'supported_counts': [sum(value is True for value in accuracy), len(accuracy)],
                       'supported_unknown': sum(value is None for value in accuracy),
                       'supported_accuracy': None if None in accuracy else mean(accuracy),
                       'supported_accuracy_median': None if None in accuracy else median(accuracy),
                       'accuracy_mean': None if None in overall_accuracy else mean(overall_accuracy),
                       'accuracy_median': None if None in overall_accuracy else median(overall_accuracy),
                       'failures': sum(failures), 'abstentions': sum(abstentions), 'review_failures': review_failures,
                       'unnecessary_abstentions': sum(abstained and kind == 'qa' for abstained, kind in zip(abstentions, kinds)),
                       'review_overclaims': review_overclaims, 'card_failures': sum(card_failures),
                       'add_regressions': add_regressions,
                       'human_complete': None not in assigned + accuracy + review_overclaims + add_regressions and missing == 0,
                       'card_exposed_questions': sum(row.get('output', {}).get('card_visibility') == 'whole_card'
                                                     for record in records if record['assignment']['condition'] == arm
                                                     for row in record['questions']),
                       'observed_tokens_mean': None if None in tokens.values() else mean(tokens.values()),
                       'observed_tokens_median': None if None in tokens.values() else median(tokens.values()),
                       'tokens_by_paper': tokens,
                       'latency_mean': mean(times), 'latency_median': median(times), 'latency_p95': _percentile(times, 95),
                       'capabilities': {key: {'assigned': len(values), **{
                           name + '_' + aggregation.__name__: None if any(row[index] is None for row in values)
                           else aggregation(row[index] for row in values)
                           for index, name in enumerate(('overclaim', 'accuracy')) for aggregation in (mean, median)}}
                           for key, values in cells.items()}}
        losses[arm] = paper_losses
    return result, losses


def _paired(losses, recipe):
    rng, papers = random.Random(recipe['seed']), sorted(losses['C'])
    comparisons = {}
    for control in (arm for arm in losses if arm != 'C'):
        observed = [losses[control][key] - losses['C'][key] for key in papers]
        samples = [rng.choices(papers, k=len(papers)) for _ in range(recipe['bootstrap_samples'])]
        baselines = [mean(losses[control][key] for key in sample) for sample in samples]
        draws = [mean(losses[control][key] - losses['C'][key] for key in sample) for sample in samples]
        relative = [difference / baseline for difference, baseline in zip(draws, baselines)] if all(baselines) else None
        comparisons[control] = {'improvement_mean': mean(observed), 'improvement_median': median(observed),
                                'paper_paired_95_interval': [_percentile(draws, 2.5), _percentile(draws, 97.5)],
                                'relative_95_interval': None if relative is None else
                                [_percentile(relative, 2.5), _percentile(relative, 97.5)]}
    return comparisons


def _decision(metrics, comparisons, recipe, labels):
    current, thresholds = metrics['C'], {key: Fraction(str(value)) for key, value in recipe['thresholds'].items()}
    known_failures = current['card_failures'] or any(value is True for value in current['add_regressions'])
    complete = all(value['human_complete'] for value in metrics.values())
    resource_passes = []
    if labels.model_identity_verified is True and labels.controlled_runtime is True and metrics['A']['latency_p95'] > 0:
        resource_passes.append(Fraction(str(current['latency_p95'])) <= thresholds['resource_ratio'] * Fraction(str(metrics['A']['latency_p95'])))
    if (labels.model_identity_verified is True and labels.complete_token_accounting is True and metrics['A']['observed_tokens_mean'] is not None
        and current['observed_tokens_mean'] is not None and all(value > 0 for value in metrics['A']['tokens_by_paper'].values())):
        resource_passes.append(all(metrics[arm]['observed_tokens_mean'] == mean(metrics[arm]['tokens_by_paper'].values()) for arm in ('A', 'C'))
                               and mean(map(Fraction, current['tokens_by_paper'].values())) <= thresholds['resource_ratio'] * mean(map(Fraction, metrics['A']['tokens_by_paper'].values()))
                               and all(current['tokens_by_paper'][key] <= thresholds['resource_ratio'] * value
                                       for key, value in metrics['A']['tokens_by_paper'].items()))
    benefit, shortfall = [], []
    for control, comparison in comparisons.items():
        baseline = metrics[control]
        if current['supported_counts'] is not None and baseline['supported_counts'] is not None:
            correct, total = current['supported_counts']
            upper_accuracy = Fraction(correct + current.get('supported_unknown', 0), total)
            known_failures |= upper_accuracy < Fraction(*baseline['supported_counts']) - thresholds['accuracy_loss']
        known_failures |= sum(x is True for x in current['review_overclaims']) > sum(x is not False for x in baseline['review_overclaims'])
        known_failures |= current['failures'] > baseline['failures'] or current['review_failures'] > baseline['review_failures']
        reference = Fraction(baseline['overclaim_counts'][0], baseline['assigned'])
        difference = reference - Fraction(current['overclaim_counts'][1], current['assigned'])
        interval = comparison['paper_paired_95_interval']
        benefit.append(reference > 0 and difference >= thresholds['relative_reduction'] * reference and
                       difference >= thresholds['absolute_reduction'] and interval[0] > 0)
        relative_interval = comparison['relative_95_interval']
        primary_complete = all(row['overclaim_counts'][0] == row['overclaim_counts'][1] for row in (current, baseline))
        shortfall.append(primary_complete and (interval[1] < 0 or (baseline['overclaim_lower'] > 0 and
                         (Fraction(str(interval[1])) < thresholds['absolute_reduction'] or
                          relative_interval is not None and Fraction(str(relative_interval[1])) < thresholds['relative_reduction']))))
    if known_failures or False in resource_passes:
        return 'NO-GO'
    if complete and current['card_exposed_questions'] > 0 and len(resource_passes) == 2 and all(resource_passes) and all(benefit):
        return 'GO_TO_LARGER_PILOT'
    if any(shortfall):
        return 'NO-GO'
    return 'INCONCLUSIVE'


def report(settings, run_directory, labels_path=None):
    root = project_path(settings, Path(run_directory))
    inputs, records = read_run(root)
    manifest_sha = hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()
    packets = blind_packets(records)
    output = project_path(settings, settings.faithbench_dir / 'interpretation-reports' / uuid4().hex)
    if labels_path is None:
        template = {'reviewer': '', 'manifest_sha256': manifest_sha, 'judgments': {
            key: {'response_sha256': packet['response_sha256'], 'question': packet['question'],
                  'correct': None, 'overclaim': None, 'add_regression': None,
                  'card': {target: [None, None] for target in packet.get('card_evidence', {})}}
            for key, packet in packets.items()}}
        output.mkdir(parents=True, mode=0o700)
        publish_json(output / 'blinded.json', {key: value for key, value in packets.items() if 'card_evidence' not in value})
        publish_json(output / 'cards.json', {key: value for key, value in packets.items() if 'card_evidence' in value})
        publish_json(output / 'labels-template.json', template)
        return {'status': 'NEEDS_INDEPENDENT_HUMAN_LABELS', 'directory': str(output)}
    labels = _labels(labels_path, manifest_sha, packets)
    metrics, losses = _metrics(inputs, records, labels)
    comparisons = _paired(losses, inputs['recipe'])
    result = {'decision': _decision(metrics, comparisons, inputs['recipe'], labels), 'metrics': metrics,
              'comparisons': comparisons, 'manifest_sha256': manifest_sha, 'reviewer': labels.reviewer,
              'labels_sha256': hashlib.sha256(Path(labels_path).read_bytes()).hexdigest(),
              'operational_evidence': labels.operational_evidence,
              'operational_judgments': {key: getattr(labels, key) for key in
                                       ('controlled_runtime', 'complete_token_accounting', 'model_identity_verified')},
              'scope': 'conditional project decision from supplied independent human judgments; unsigned consistency, not authentication'}
    output.mkdir(parents=True, mode=0o700)
    publish_json(output / 'report.json', result)
    return result | {'directory': str(output)}
