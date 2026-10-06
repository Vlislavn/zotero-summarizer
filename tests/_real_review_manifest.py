"""Explicit private freeze of existing cached inputs; never generates reviews.

CLI: python -m tests._real_review_manifest CACHE OUTPUT --api-base URL
Output contains private raw snapshots. Do not commit it or its receipts.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]


def repeated_pair(entry):
    goals = entry.get('goal_summaries') or []
    for i, left in enumerate(goals):
        for right in goals[i + 1:]:
            shared = set(sentences(left.get('summary') or '')) & set(sentences(right.get('summary') or ''))
            if (shared and left.get('goal') != right.get('goal')
                    and (left.get('supporting_quotes') or right.get('supporting_quotes'))):
                return True
    return False


def select(reviews):
    ordered = sorted(reviews, key=lambda key: hashlib.sha256(key.encode()).hexdigest())
    first = next(key for key in ordered if repeated_pair(reviews[key]))
    candidates = [key for key in ordered if key != first and
                  len({g['goal'] for g in reviews[key].get('goal_summaries') or []}) >= 6]
    second = max(candidates, key=lambda key: len(json.dumps(reviews[key], ensure_ascii=False)))
    third = next(key for key in ordered if key not in {first, second} and any(
        g.get('retrieval_state') != 'hit' or not g.get('relevant') or g.get('abstained')
        for g in reviews[key].get('goal_summaries') or []))
    return [first, second, third]


def freeze(cache, output, api_base):
    before = cache.read_bytes()
    reviews = json.loads(before)['reviews']
    rules = ['first hashed identity with identical sentence across distinct goal summaries and a quote',
             'longest serialized distinct remaining raw entry with at least six distinct goals; hash-order tie break',
             'first hashed remaining identity with non-hit, unsupported or abstained goal']
    cases = []
    for index, key in enumerate(select(reviews)):
        url = api_base.rstrip('/') + '/api/golden/review-detail?' + urlencode({'item_key': key})
        with urlopen(url) as response:
            detail = json.load(response)
        title = detail.get('title') or reviews[key].get('title')
        if not title:
            raise ValueError('Readonly metadata did not supply a title')
        cases.append({'case_id': hashlib.sha256(key.encode()).hexdigest()[:16],
                      'source_item_key': key, 'title': title, 'selection_rule': rules[index],
                      'raw_entry': reviews[key], 'raw_entry_sha256': digest(reviews[key])})
    after = cache.read_bytes()
    if before != after:
        raise ValueError('Source changed during freeze')
    manifest = {'schema_version': 1, 'source_sha256': hashlib.sha256(before).hexdigest(),
                'source_sha256_after': hashlib.sha256(after).hexdigest(), 'source_count': len(reviews),
                'historical_designated_example': 'unknown; long case is a newly selected proxy prototype example',
                'acceptance': 'machine retention only; no human or scientific-truth claim', 'cases': cases}
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    output.chmod(0o600)
    return manifest


def load_manifest(path):
    manifest = json.loads(Path(path).read_text())
    cases = manifest['cases']
    if (manifest['schema_version'] != 1 or len(cases) != 3 or
            len({c['case_id'] for c in cases}) != 3 or
            manifest['source_sha256'] != manifest['source_sha256_after']):
        raise ValueError('Invalid frozen three-case manifest')
    for case in cases:
        if digest(case['raw_entry']) != case['raw_entry_sha256']:
            raise ValueError('Frozen entry hash mismatch')
    if not repeated_pair(cases[0]['raw_entry']):
        raise ValueError('Repeated-summary selection obligation missing')
    if len({g['goal'] for g in cases[1]['raw_entry']['goal_summaries']}) < 6:
        raise ValueError('Long-board selection obligation missing')
    if not any(g.get('retrieval_state') != 'hit' or not g.get('relevant') or g.get('abstained')
               for g in cases[2]['raw_entry']['goal_summaries']):
        raise ValueError('State selection obligation missing')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--api-base', required=True)
    args = parser.parse_args()
    freeze(args.cache, args.output, args.api_base)
