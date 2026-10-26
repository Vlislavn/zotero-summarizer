"""Unsigned declared ancestry: bounded closure, exclusion inventory and actual-use binding.

This never regenerates contaminated text/checkpoints or authenticates source declarations.
"""
from collections import defaultdict, deque

from .collections import identity, reference_safe
from .support import digest, normalized


def version_key(paper):
    if paper.version_id is None:
        raise ValueError('declared lineage requires exact paper-version identities')
    return paper.paper_id, paper.version_id


def checked_leaves(entries, inputs, truth):
    leaves, papers, versions, families = {}, {}, {}, {}
    for entry in entries.values():
        paper = entry.paper
        if paper is None:
            continue
        key = version_key(paper)
        previous = versions.get(paper.version_id)
        collision = previous is not None and (
            previous.model_dump(exclude={'paper_id'}) != paper.model_dump(exclude={'paper_id'}))
        if (key in leaves or paper.paper_id in papers or collision
                or entry.observed_at != paper.observed_at
                or entry.payload_sha256 != digest(paper.model_dump(mode='json'))):
            raise ValueError('duplicate/colliding paper-version or source binding')
        if paper.family in families and families[paper.family] != paper.split:
            raise ValueError('whole-family lineage split leakage')
        if paper.split == 'development' and paper.observed_at >= truth.split_frozen_at:
            raise ValueError('late development lineage source')
        leaves[key], papers[paper.paper_id], versions[paper.version_id] = entry.entry_id, paper, paper
        families[paper.family] = paper.split
    for paper in inputs.papers:
        if papers.get(paper.paper_id) != paper or version_key(paper) not in leaves:
            raise ValueError('candidate missing from exact lineage inventory')
    return leaves


def closure(entries, limit):
    work = len(entries) + sum(len(e.source_ids) for e in entries.values())
    if work > limit:
        raise ValueError('lineage work budget exceeded')
    children, pending, ancestors = defaultdict(list), {}, {}
    for key, entry in entries.items():
        pending[key] = len(entry.source_ids)
        for source in entry.source_ids:
            if source not in entries:
                raise ValueError('unknown lineage source')
            if entries[source].observed_at > entry.observed_at:
                raise ValueError('derived lineage predates its source')
            children[source].append(key)
    ready = deque(k for k, n in pending.items() if n == 0)
    while ready:
        key = ready.popleft()
        sources = entries[key].source_ids
        found = {key} if not sources else set()
        for source in sources:
            work += len(ancestors[source])
            if work > limit:
                raise ValueError('transitive lineage work budget exceeded')
            found.update(ancestors[source])
        ancestors[key] = found
        for child in children[key]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    if len(ancestors) != len(entries):
        raise ValueError('cyclic lineage')
    return ancestors, work


def excluded_sources(entries, inputs):
    held = [p for p in inputs.papers if p.split == 'holdout']
    held += [e.paper for e in entries.values() if e.paper and e.paper.split == 'holdout']
    families = {p.family for p in held}
    aliases = {normalized(a) for p in held for a in p.canonical_ids}
    texts = {identity(p) for p in held}
    return {k for k, e in entries.items() if e.paper is not None and (
        e.paper.family in families or identity(e.paper) in texts or
        aliases.intersection(normalized(a) for a in e.paper.canonical_ids))}


def expected_uses(cfg, inputs, artifacts):
    tree, expected = inputs.tree, {}
    for node in tree.nodes:
        if node.description:
            expected['description', node.key] = (node.model_dump(mode='json'), tree.frozen_at, tree.description_sources)
    for index, example in enumerate(tree.examples):
        expected['example', str(index)] = (example.model_dump(mode='json'), tree.frozen_at, (example.paper,))
    if tree.selected_keys is not None:
        expected['shortlist', 'tree'] = ({'selected_keys': list(tree.selected_keys),
            'selection_provenance': tree.selection_provenance}, tree.frozen_at, None)
    if cfg.calibration:
        cal = cfg.calibration
        sources = tuple(p for p in inputs.papers if p.split == 'development'
                        and p.family in cal.development_families)
        expected['calibration', 'calibration'] = (cal.model_dump(mode='json'), cal.frozen_at, sources)
    if artifacts is not None:
        for arm_id, artifact in artifacts.items():
            if artifact.reference_papers:
                expected['baseline', arm_id] = (artifact.model_dump(mode='json'), artifact.frozen_at,
                                               artifact.reference_papers)
    return expected


def checked_uses(cfg, inputs, truth, graph, artifacts):
    entries, ancestors, removed, leaves = graph
    expected = expected_uses(cfg, inputs, artifacts)
    uses = {(u.channel, u.owner): u for u in inputs.lineage.uses}
    if len(uses) != len(inputs.lineage.uses):
        raise ValueError('duplicate lineage use')
    baseline_keys = {('baseline', a.arm_id) for a in cfg.baselines if a.artifact}
    observed_keys = set(uses) - (baseline_keys if artifacts is None else set())
    if observed_keys != set(expected):
        raise ValueError('missing/foreign lineage reference-use binding')
    for key, use in uses.items():
        if use.entry_id not in entries or use.entry_id in removed:
            raise ValueError('reference use depends on excluded/unknown lineage')
        if key not in expected:
            continue  # Baseline bytes are admitted at prepare's existing artifact boundary.
        value, frozen_at, sources = expected[key]
        entry = entries[use.entry_id]
        if (use.binding_sha256 != digest(value) or entry.payload_sha256 != use.binding_sha256
                or entry.observed_at > frozen_at):
            raise ValueError('lineage use/content/time binding mismatch')
        actual = ancestors[use.entry_id]
        for source in actual:
            reference_safe(entries[source].paper, inputs, truth, frozen_at)
        if sources is not None:
            wanted = set()
            for paper in sources:
                source = leaves.get(version_key(paper))
                if source is None or entries[source].paper != paper:
                    raise ValueError('reference paper-version/source mismatch')
                wanted.add(source)
            if key[0] == 'description':
                if not actual <= wanted:
                    raise ValueError('undeclared description source')
            elif actual != wanted:
                raise ValueError('reference lineage inventory differs from actual use')
    descriptions = set().union(*(ancestors[u.entry_id] for k, u in uses.items() if k[0] == 'description'))
    declared = {leaves.get(version_key(p)) for p in inputs.tree.description_sources}
    if descriptions != declared:
        raise ValueError('unused/missing description source inventory')
    return uses


def lineage_audit(cfg, inputs, truth, artifacts=None):
    lineage = inputs.lineage
    if lineage is None:
        return None, 0
    entries = {e.entry_id: e for e in lineage.entries}
    inventory = lineage.inventory_ids
    inventory_keys = set(inventory)
    if (not entries or len(entries) != len(lineage.entries) or len(inventory_keys) != len(inventory)
            or not inventory_keys <= entries.keys()):
        raise ValueError('nonempty unique lineage entries and known inventory required')
    if any((key in inventory_keys) != (entry.channel is not None) for key, entry in entries.items()):
        raise ValueError('original reference inventory requires explicit channels only')
    leaves = checked_leaves(entries, inputs, truth)
    ancestors, work = closure(entries, cfg.max_work_units)
    work += len(lineage.uses) + len(inventory)
    work += sum(len(ancestors.get(use.entry_id, ())) for use in lineage.uses)
    if work > cfg.max_work_units:
        raise ValueError('lineage use/inventory work budget exceeded')
    excluded = excluded_sources(entries, inputs)
    removed = {k: sorted(sources & excluded) for k, sources in ancestors.items() if sources & excluded}
    uses = checked_uses(cfg, inputs, truth, (entries, ancestors, removed, leaves), artifacts)
    roots = set(inventory) | {u.entry_id for u in uses.values()} | {leaves[version_key(p)] for p in inputs.papers}
    reachable, pending = set(), list(roots)
    while pending:
        key = pending.pop()
        if key not in reachable:
            reachable.add(key)
            pending.extend(entries[key].source_ids)
    if reachable != entries.keys():
        raise ValueError('orphan lineage entry outside declared inventory/use/candidates')
    audit = {'qualification': 'declared_only_not_authenticated',
        'active_reference_ids': [k for k in inventory if k not in removed],
        'removed': [{'record': e.model_dump(mode='json'), 'heldout_source_ids': removed[k]}
                    for k, e in entries.items() if k in removed],
        'uses': [u.model_dump(mode='json') for u in lineage.uses]}
    return audit, work
