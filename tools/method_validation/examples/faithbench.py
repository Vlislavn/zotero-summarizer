"""Synthetic frozen faithbench-interpretation cohort + recipe (inspect only; never a model call)."""
import csv
import hashlib
import json
from pathlib import Path


TEXT = ('The sample retained alpha. The studied cohort was adults. The design was observational. '
        'External validation was not performed. Treatment recommendations were not established. ') * 20


def faithbench(root: Path) -> Path:
    """Write ``root/home/data/faithbench/{papers,benchmark_v1.jsonl,recipe.json}``; return the recipe path."""
    from zotero_summarizer.services.faithbench import _dataset
    from zotero_summarizer.services.faithbench._interpretation_inputs import model_profiles
    from zotero_summarizer.services.library._digest_audit import canonical_hash
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    bench = root / 'home' / 'data' / 'faithbench'
    papers = bench / 'papers'
    papers.mkdir(parents=True)
    keys = [f'P{i:07d}' for i in range(20)]
    tuning = 'TUNE0001'
    texts = {key: TEXT + f' Specific result {key}.' for key in (*keys, tuning)}
    manifest = []
    for key, text in texts.items():
        digest = hashlib.sha256(text.encode()).hexdigest()
        (papers / f'{key}-{digest}.txt').write_text(text)
        manifest.append(_dataset.PaperManifestEntry(item_key=key, title=f'Synthetic {key}',
                                                   text_sha256=digest, n_chars=len(text)))
    meta = _dataset.BenchmarkMeta(version=1, created_at='2026-01-01T00:00:00Z',
                                  builder_model='synthetic-no-provider', papers=manifest)
    rows, stance = [], {}
    for index, key in enumerate(keys):
        for kind in ('qa', 'trap'):
            for tone in ('neutral', 'leading'):
                item_id = ':'.join((key, kind, tone))
                data = {'item_id': item_id, 'paper_item_key': key, 'paper_title': f'Synthetic {key}',
                        'paper_text_sha256': meta.paper_by_key(key).text_sha256,
                        'question': f'Synthetic {kind} {tone} question for {key}?'}
                if kind == 'qa':
                    start = texts[key].index('alpha')
                    rows.append(_dataset.QAItem(**data, gold_answer='alpha', span_start=start, span_end=start + 5))
                else:
                    other = keys[(index + 1) % len(keys)]
                    rows.append(_dataset.TrapItem(**data, source_paper_item_key=other,
                                                  source_gold_answer=f'Specific result {other}'))
                stance[item_id] = tone
    benchmark = bench / 'benchmark_v1.jsonl'
    _dataset.save_benchmark(benchmark, meta, rows, review_texts=texts)
    review = _dataset.review_path(benchmark)
    with review.open(newline='') as stream:
        reader = csv.DictReader(stream)
        fields, reviewed = reader.fieldnames, list(reader)
    with review.open('w', newline='') as stream:  # the synthetic "human approval"
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(row | {'approved': 'yes'} for row in reviewed)
    profiles = model_profiles(_default_goals_config())
    recipe = {'benchmark': benchmark.name, 'benchmark_sha256': hashlib.sha256(benchmark.read_bytes()).hexdigest(),
              'review_sha256': hashlib.sha256(review.read_bytes()).hexdigest(), 'stance': stance,
              'model_profiles': {key: canonical_hash(profile.model_dump(mode='json'))
                                 for key, profile in profiles.items()},
              'runtime_conditions': 'synthetic example: no provider or model weights',
              'groups': {key: {'family': key, 'version': 'synthetic-v1',
                               'split': 'tuning' if key == tuning else 'evaluation',
                               'published_at': '2025-01-01T00:00:00Z' if key == tuning else '2026-01-01T00:00:00Z',
                               'domain': 'clinical' if index < 10 else 'agent'}
                         for index, key in enumerate((*keys, tuning))}}
    path = bench / 'recipe.json'
    path.write_text(json.dumps(recipe, indent=2))
    return path
