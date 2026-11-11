"""Synthetic protocol fixtures, not human-reviewed papers or scientific evidence."""
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from tests._interpretation_experiment import TEXT, inputs
from zotero_summarizer.services.faithbench._interpretation_inputs import model_profiles
from zotero_summarizer.services.faithbench import _dataset
from zotero_summarizer.services.library._digest_audit import canonical_hash


def cohort(tmp_path):
    root = tmp_path / 'faithbench'
    papers = root / 'papers'
    papers.mkdir(parents=True)
    keys = ['P' + str(i).zfill(7) for i in range(20)]
    tuning = 'TUNE0001'
    texts = {key: TEXT + f' Specific result {key}.' for key in keys + [tuning]}
    manifest = []
    for key, text in texts.items():
        sha = hashlib.sha256(text.encode()).hexdigest()
        (papers / f'{key}-{sha}.txt').write_text(text)
        manifest.append(_dataset.PaperManifestEntry(item_key=key, title='Synthetic ' + key,
                                                   text_sha256=sha, n_chars=len(text)))
    meta = _dataset.BenchmarkMeta(version=1, created_at='2026-01-01T00:00:00Z',
                                  builder_model='synthetic-no-provider', papers=manifest)
    rows, stance = [], {}
    for index, key in enumerate(keys):
        for kind in ('qa', 'trap'):
            for tone in ('neutral', 'leading'):
                item_id = ':'.join((key, kind, tone))
                data = dict(item_id=item_id, paper_item_key=key, paper_title='Synthetic ' + key,
                            paper_text_sha256=meta.paper_by_key(key).text_sha256,
                            question=f'Synthetic {kind} {tone} question for {key}?')
                if kind == 'qa':
                    answer = 'alpha'
                    start = texts[key].index(answer)
                    row = _dataset.QAItem(**data, gold_answer=answer, span_start=start, span_end=start + len(answer))
                else:
                    other = keys[(index + 1) % len(keys)]
                    row = _dataset.TrapItem(**data, source_paper_item_key=other,
                                            source_gold_answer=f'Specific result {other}')
                rows.append(row)
                stance[item_id] = tone
    benchmark = root / 'benchmark_v1.jsonl'
    _dataset.save_benchmark(benchmark, meta, rows, review_texts=texts)
    review = _dataset.review_path(benchmark)
    with review.open(newline='') as stream:
        reader = csv.DictReader(stream)
        fields, approved = reader.fieldnames, list(reader)
    with review.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(row | {'approved': 'yes'} for row in approved)
    _, config, _ = inputs()
    profiles = model_profiles(config)
    recipe = {'benchmark': benchmark.name, 'benchmark_sha256': hashlib.sha256(benchmark.read_bytes()).hexdigest(),
              'review_sha256': hashlib.sha256(review.read_bytes()).hexdigest(), 'stance': stance,
              'model_profiles': {key: canonical_hash(profile.model_dump(mode='json')) for key, profile in profiles.items()},
              'runtime_conditions': 'synthetic fixture: no provider or model weights',
              'groups': {key: {'family': key, 'version': 'synthetic-v1', 'split': 'tuning' if key == tuning else 'evaluation',
                               'published_at': '2025-01-01T00:00:00Z' if key == tuning else '2026-01-01T00:00:00Z',
                               'domain': 'clinical' if index < 10 else 'agent'}
                         for index, key in enumerate(keys + [tuning])}}
    path = root / 'recipe.json'
    path.write_text(json.dumps(recipe))
    return SimpleNamespace(faithbench_dir=root), path, recipe, config, profiles


def changed_recipe(path, recipe, mutate):
    value = json.loads(json.dumps(recipe))
    mutate(value)
    path.write_text(json.dumps(value))
    return path
