"""Freeze admitted experiment inputs before constructing any provider client."""
from datetime import datetime
import hashlib
from importlib.metadata import distributions
import json
import sys
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from zotero_summarizer.services.faithbench import _dataset
from zotero_summarizer.services.faithbench._corpus import load_frozen_text
from zotero_summarizer.services.library._digest_audit import canonical_hash

Hash = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


class Group(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    family: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    published_at: datetime
    split: Literal['tuning', 'evaluation']
    domain: Literal['clinical', 'agent']


class Thresholds(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    relative_reduction: float = Field(default=0.30, ge=0.30, le=1)
    absolute_reduction: float = Field(default=0.05, ge=0.05, le=1)
    accuracy_loss: float = Field(default=0.05, ge=0, le=0.05)
    resource_ratio: float = Field(default=1.25, ge=1, le=1.25)


class Recipe(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    benchmark: str
    benchmark_sha256: Hash
    review_sha256: Hash
    groups: dict[str, Group]
    stance: dict[str, Literal['neutral', 'leading']]
    model_profiles: dict[Literal['map', 'reduce', 'qa'], Hash]
    runtime_conditions: Annotated[str, Field(min_length=1)]
    labels: dict[Literal['A', 'B', 'C'], str] = Field(default_factory=lambda: {'A': 'current', 'B': 'caution', 'C': 'caution+card'})
    minimum_papers: int = Field(default=20, ge=20)
    context_chars: int = Field(default=60000, ge=1, le=1000000)
    chunk_chars: int = Field(default=8000, ge=1000, le=1000000)
    sub_concurrency: int = Field(default=1, ge=1, le=16)
    max_client_calls: int = Field(default=256, ge=1, le=4096)
    bootstrap_samples: int = Field(default=2000, ge=100, le=100000)
    seed: int = 49
    thresholds: Thresholds = Field(default_factory=Thresholds)


def runtime_identity():
    return {'python': sys.version, 'distributions': sorted([d.metadata['Name'], d.version] for d in distributions())}


def code_identity():
    root = Path(__file__).parents[2]
    return canonical_hash({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted(root.rglob('*.py'))})


def _cohort(recipe, meta, items, texts):
    groups = recipe.groups
    if set(groups) != set(texts) or set(recipe.stance) != {item.item_id for item in items}:
        raise ValueError('Complete paper/family/cell metadata required')
    evaluation = {key for key, group in groups.items() if group.split == 'evaluation'}
    tuning = set(groups) - evaluation
    if len(evaluation) < recipe.minimum_papers or not tuning:
        raise ValueError('Experiment requires held-out papers and separate tuning sources')
    if {groups[key].domain for key in evaluation} != {'clinical', 'agent'}:
        raise ValueError('Clinical and agent paper domains are required')
    evaluation_hashes = {meta.paper_by_key(key).text_sha256 for key in evaluation}
    if (len(evaluation_hashes) != len(evaluation) or len({groups[key].family for key in evaluation}) != len(evaluation) or
        {groups[key].family for key in evaluation} & {groups[key].family for key in tuning} or
        evaluation_hashes & {meta.paper_by_key(key).text_sha256 for key in tuning}):
        raise ValueError('Tuning/evaluation family or extraction leakage')
    if any(group.published_at.utcoffset() is None for group in groups.values()):
        raise ValueError('Timezone-qualified source times required')
    if max(groups[key].published_at for key in tuning) >= min(groups[key].published_at for key in evaluation):
        raise ValueError('Tuning must precede held-out source versions')
    for key in evaluation:
        rows = [item for item in items if item.paper_item_key == key]
        if len(rows) != 4 or {(item.kind, recipe.stance[item.item_id]) for item in rows} != {
            ('qa', 'neutral'), ('qa', 'leading'), ('trap', 'neutral'), ('trap', 'leading')
        }:
            raise ValueError('Exactly four answerability/stance cells per paper required')
    for item in items:
        _dataset._validate_item(item, meta, texts)
        if item.paper_item_key not in evaluation or getattr(item, 'source_paper_item_key', None) in tuning:
            raise ValueError('Questions cannot use tuning papers')
    return sorted(evaluation)


def project_path(settings, path):
    root, path = settings.faithbench_dir, Path(path)
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Interpretation path escapes selected project')
    boundary = getattr(settings, 'project_root', root.parent)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError('Symlinked interpretation paths are not admitted')
        if ancestor == boundary:
            break
    return path


def inspect(settings, recipe_path):
    raw = Path(recipe_path).read_bytes()
    recipe = Recipe.model_validate_json(raw)
    if set(recipe.model_profiles) != {'map', 'reduce', 'qa'} or set(recipe.labels) != {'A', 'B', 'C'}:
        raise ValueError('All three profiles and conditions are required')
    root = settings.faithbench_dir
    benchmark = project_path(settings, root / recipe.benchmark)
    meta, items = _dataset.load_benchmark(benchmark, expected_sha256=recipe.benchmark_sha256)
    texts = {p.item_key: load_frozen_text(root / 'papers', p.item_key, expected_sha256=p.text_sha256) for p in meta.papers}
    evaluation = _cohort(recipe, meta, items, texts)
    csv = project_path(settings, _dataset.review_path(benchmark)).read_bytes()
    if hashlib.sha256(csv).hexdigest() != recipe.review_sha256:
        raise ValueError('Approved reference CSV changed')
    if _dataset.require_review_approval(benchmark, items) != recipe.review_sha256:
        raise ValueError('Approved reference CSV changed during inspection')
    return {'recipe': recipe, 'recipe_bytes': raw, 'meta': meta, 'items': items, 'texts': texts,
            'evaluation': evaluation, 'benchmark': benchmark.read_bytes(), 'review_csv': csv}


def validate_frozen(root, inputs):
    recipe = Recipe.model_validate_json((root / 'recipe.json').read_bytes())
    meta, items = _dataset.load_benchmark(root / 'benchmark.jsonl', expected_sha256=recipe.benchmark_sha256)
    if (recipe.model_dump(mode='json') != inputs['recipe'] or meta.model_dump(mode='json') != inputs['meta'] or
        [item.model_dump(mode='json') for item in items] != inputs['items'] or
        _dataset.require_review_approval(root / 'benchmark.jsonl', items) != recipe.review_sha256 or
        {key: canonical_hash(value) for key, value in inputs['models'].items()} != recipe.model_profiles):
        raise ValueError('Frozen benchmark/recipe/profile identities changed')
    for paper in meta.papers:
        text = inputs['texts'][paper.item_key]
        if len(text) != paper.n_chars or hashlib.sha256(text.encode()).hexdigest() != paper.text_sha256:
            raise ValueError('Frozen extraction changed')
    if (inputs['runtime'] != runtime_identity() or _cohort(recipe, meta, items, inputs['texts']) != inputs['evaluation']
        or inputs['code'] != code_identity()):
        raise ValueError('Frozen cohort/code identity changed')


def publish_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        path.chmod(0o600)
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)


def model_profiles(config):
    from zotero_summarizer.models.providers import resolve_stage
    mapper = 'feed' if config.quality_review.chunk_strategy == 'map_reduce' else 'deep_review'
    return {role: resolve_stage(config.llm_routing, stage) for role, stage in
            (('map', mapper), ('reduce', 'deep_review'), ('qa', 'deep_review'))}


def prepare(settings, snapshot, config, profiles):
    recipe = snapshot['recipe']
    if (profiles != model_profiles(config) or
        {key: canonical_hash(value.model_dump(mode='json')) for key, value in profiles.items()} != recipe.model_profiles):
        raise ValueError('Resolved model profiles changed before client construction')
    if recipe.sub_concurrency > 1 and any(profile.provider.is_local for profile in profiles.values()):
        raise ValueError('Local model calls must remain single-instance and serial')
    parent = project_path(settings, settings.faithbench_dir / 'interpretation')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = parent / uuid4().hex
    root.mkdir(mode=0o700)
    benchmark = root / 'benchmark.jsonl'
    for path, raw in ((benchmark, snapshot['benchmark']), (_dataset.review_path(benchmark), snapshot['review_csv']),
                      (root / 'recipe.json', snapshot['recipe_bytes'])):
        with path.open('xb') as stream:
            path.chmod(0o600)
            stream.write(raw)
    meta, items = _dataset.load_benchmark(benchmark, expected_sha256=recipe.benchmark_sha256)
    if _dataset.require_review_approval(benchmark, items) != recipe.review_sha256:
        raise ValueError('Frozen human approval changed')
    inputs = {'recipe': recipe.model_dump(mode='json'), 'meta': meta.model_dump(mode='json'),
              'items': [item.model_dump(mode='json') for item in items], 'texts': snapshot['texts'],
              'evaluation': snapshot['evaluation'], 'code': code_identity(),
              'config_sha256': canonical_hash(config.model_dump(mode='json')),
              'models': {key: value.model_dump(mode='json') for key, value in profiles.items()},
              'runtime_conditions': recipe.runtime_conditions, 'runtime': runtime_identity(),
              'runtime_observation': 'not_observed',
              'remote_weights_identity': 'not_established', 'physical_retries': 'not_observed'}
    publish_json(root / 'inputs.json', inputs)
    return root, inputs
