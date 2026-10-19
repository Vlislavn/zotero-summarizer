"""Explicit collection comparison with fresh-owner publication and verified offline replay."""
import os
from pathlib import Path
from time import monotonic

from .collections import (BaselineArtifact, Config, Inputs, PaperTiming, Readout, Result, Truth,
                          admit, identity, payload, project, reference_safe)
from .collections_report import readable, report
from .decision_adapter import NativeDecisionAdapter, ProviderReply, endpoint
from .support import digest, encoded, loads, read_bound, sha
from .support_run import check_unattempted, code_identity, publish, read_owned, unattempted


def schema_identity():
    return digest([m.model_json_schema() for m in (Config, Inputs, Truth, BaselineArtifact, Readout, Result, PaperTiming)])


def baseline_owner():
    root = Path(__file__).resolve().parents[2] / 'zotero_summarizer/storage'
    return digest({n: sha((root / n).read_bytes()) for n in ('corpus_read.py', 'corpus_types.py')})


def units(papers, questions, cfg):
    for key in papers:
        for arm in (*cfg.arms, *cfg.baselines):
            for node in (None,) if arm.kind == 'similarity' else questions:
                yield (digest([key, node]), key, node, arm)


def prepare(cfg, snapshots=None):
    saved, admitted_bytes = {} if snapshots is None else dict(snapshots), 0
    def read(name, ref, cap):
        nonlocal admitted_bytes
        if snapshots is None:
            saved[name] = read_bound(ref, cap)
        if name not in saved:
            raise FileNotFoundError('frozen snapshot unavailable')
        raw = saved[name]
        admitted_bytes += len(raw)
        if len(raw) > cap or sha(raw) != ref.sha256 or admitted_bytes > cfg.max_snapshot_bytes:
            raise ValueError('snapshot byte/hash/cumulative budget mismatch')
        return raw
    inputs = Inputs.model_validate(loads(read('inputs.json', cfg.inputs, cfg.max_input_bytes)))
    truth = Truth.model_validate(loads(read('gold.json', cfg.gold, cfg.max_input_bytes)))
    papers, gold, nodes, paths, questions = admit(cfg, inputs, truth)
    count = len(papers) * (len(questions) * len(cfg.arms) + len(cfg.baselines))
    if count > cfg.max_work_units:
        raise ValueError('whole outcome workload exceeds budget')
    requests, errors = {}, {}
    for key, paper in papers.items():
        for node in questions:
            try:
                request = payload(paper, node, (nodes, paths, inputs.tree), cfg)
                admitted_bytes += len(request)
                if admitted_bytes > cfg.max_snapshot_bytes:
                    raise ValueError('cumulative request/snapshot budget exceeded')
                requests[digest([key, node])] = request
            except ValueError as error:
                if error.args[0] not in ('missing_input', 'input_too_large'):
                    raise
                errors[key] = error.args[0]
    baselines = {}
    for index, arm in enumerate(cfg.baselines):
        rows = {}
        if arm.artifact:
            artifact = BaselineArtifact.model_validate(loads(read(f'baseline-{index}.json', arm.artifact, cfg.max_input_bytes)))
            if artifact.model != arm.model or artifact.owner_sha256 != baseline_owner():
                raise ValueError('baseline model/source binding mismatch')
            if artifact.frozen_at >= truth.split_frozen_at:
                raise ValueError('baseline must be frozen before split opening')
            if not artifact.reference_papers and any(isinstance(r.get('readout'), dict)
                    and r['readout'].get('has_corpus') is True for r in artifact.rows):
                raise ValueError('corpus-backed baseline requires reference lineage inventory')
            for paper in artifact.reference_papers:
                reference_safe(paper, inputs, truth, artifact.frozen_at)
            rows = {r.get('case_sha256'): r for r in artifact.rows}
            if len(rows) != len(artifact.rows) or not rows.keys() <= papers.keys():
                raise ValueError('duplicate/unmatched normalized baseline identity')
        baselines[arm.arm_id] = rows
    recordings = {}
    allowed = {(u, a.arm_id) for u, _, _, a in units(papers, questions, cfg) if a.kind == 'recorded'}
    for index, recording in enumerate(inputs.recordings):
        key = (recording.case_sha256, recording.arm_id)
        if key not in allowed or key in recordings:
            raise ValueError('duplicate/unmatched question/arm recording')
        recordings[key] = read(f'recording-{index}.bin', recording.terminal, cfg.max_response_bytes)
    active = [a for a in cfg.arms if a.kind == 'native' and not a.blockage()]
    calls = len(papers) * len(questions)
    if (len(active) * calls > cfg.max_calls
            or count + calls * sum(a.forward_passes_bound for a in active) > cfg.max_work_units):
        raise ValueError('whole-workload call/physical-work reservation exceeds budget')
    for arm in active:
        endpoint(arm.endpoint)
    if active and not (cfg.public_input_consent and cfg.external_guard):
        raise ValueError('live HTTP requires public-input consent and external resource guard')
    return inputs, truth, papers, gold, nodes, questions, requests, errors, baselines, recordings, saved


def base_row(unit, paper, node, arm, gold):
    key = identity(paper)
    target = gold.get(key)
    return {'case_sha256': unit, 'paper_sha256': key, 'paper_id': paper.paper_id, 'family': paper.family,
            'split': paper.split, 'stratum': paper.stratum, 'node_key': node, 'arm_id': arm.arm_id,
            'capability': 'top3_suggestions' if arm.kind == 'similarity' else 'native_membership',
            'gold_memberships': list(target.memberships) if target and target.human_provenance
                and target.memberships is not None else None,
            'status': 'unavailable', 'label': None, 'selected_keys': None, 'readout': None,
            'baseline_native': None, 'telemetry': {'reserved': False, 'dispatched': False, 'elapsed_seconds': None}}


def baseline_result(row, arm, native, nodes):
    if native is None:
        return row
    row['baseline_native'] = native
    try:
        fields = native['readout']
        names = fields['suggested_collections']
        if (set(native) != {'case_sha256', 'observed_model', 'readout'}
                or type(fields['has_corpus']) is not bool or not isinstance(names, list)
                or len(names) > 3 or any(not isinstance(n, str) or not n for n in names)
                or len(set(names)) != len(names)):
            raise ValueError('malformed original top3 result')
        selected = []
        for name in names:
            matches = [n for n in nodes.values() if n.name == name]
            if len(matches) != 1:
                raise ValueError('unknown/ambiguous original collection name')
            if matches[0].role != 'workflow':
                selected.append(matches[0].key)
        if native['observed_model'] != arm.model:
            row['status'] = 'model_mismatch'
        elif fields['has_corpus']:
            row.update(status='decision', selected_keys=selected)
    except (KeyError, TypeError, ValueError):
        row.update(status='invalid_response', reason='malformed/unknown/ambiguous top3 artifact')
    return row


def evaluate(row, arm, cfg, request, retain, *, recording=None, transport=None, remaining=None):
    row['request_sha256'] = sha(request)
    state, reason = unattempted(arm, remaining, recording)
    if state:
        row.update(status=state, reason=reason)
        row['telemetry'].update(remaining_seconds=remaining, reserved=arm.kind == 'native' and not arm.blockage())
        return row
    if arm.kind == 'recorded':
        retain(recording)
        status, readout = project(recording, arm, cfg)
        reply = ProviderReply(recording, status, readout, {'reserved': False, 'dispatched': False,
            'terminal_status': 'received', 'elapsed_seconds': None, 'client_retries': 0})
    else:
        reply = NativeDecisionAdapter(arm, cfg, transport=transport, projector=project)(
            request, retain, timeout=min(remaining, cfg.idle_timeout_seconds))
    row.update(status=reply.status, readout=reply.readout, telemetry=reply.telemetry,
               label=reply.readout['label'] if reply.status == 'decision' else None)
    return row


def run(cfg, raw_config, *, transport=None):
    prepared = prepare(cfg)
    inputs, truth, papers, gold, nodes, questions, requests, errors, baselines, recordings, saved = prepared
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=cfg.project_root, env_path=Path(os.devnull))
    parent = settings.data_dir / 'collection_comparison'
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve() != settings.data_dir.resolve() / 'collection_comparison':
        raise ValueError('artifact parent may not escape selected data_dir')
    owner = parent / cfg.attempt
    owner.mkdir(mode=0o700)
    inventory, rows, timings = {}, [], []
    def retain(name, raw):
        publish(owner / name, raw)
        inventory[name] = sha(raw)
    try:
        retain('config.original.json', raw_config)
        retain('config.json', encoded(cfg.model_dump(mode='json')))
        for name, raw in saved.items():
            retain(name, raw)
        started = monotonic()
        for unit, key, node, arm in units(papers, questions, cfg):
            if node == questions[0]:
                group_started = monotonic() if arm.kind == 'native' else None
                group = []
            row = base_row(unit, papers[key], node, arm, gold)
            if key in errors:
                row['status'] = errors[key]
            elif arm.kind == 'similarity':
                baseline_result(row, arm, baselines[arm.arm_id].get(key), nodes)
            else:
                name = f'terminal-{len(rows)}.bin'
                def native_retain(raw):
                    retain(name, raw)
                    row['terminal'] = name
                evaluate(row, arm, cfg, requests[unit], native_retain,
                         recording=recordings.get((unit, arm.arm_id)), transport=transport,
                         remaining=cfg.max_run_seconds - (monotonic() - started))
            Result.model_validate(row)
            retain(f'case-{len(rows)}.json', encoded(row))
            rows.append(row)
            if node is not None:
                group.append(row)
            if (arm.kind == 'native' and node == questions[-1]
                    and all(r['telemetry'].get('dispatched') is True for r in group)):
                timings.append(PaperTiming(paper_sha256=key, arm_id=arm.arm_id,
                    elapsed_seconds=monotonic() - group_started).model_dump(mode='json'))
        retain('paper-times.json', encoded(timings))
        result = report(rows, cfg, inputs, truth, timings=timings)
        retain('report.json', encoded(result))
        retain('report.md', readable(result).encode())
        retain('manifest.json', encoded({'version': 1, 'files': inventory, 'code': code_identity(),
               'schema': schema_identity(), 'baseline_owner': baseline_owner(), 'rows': len(rows)}))
        publish(owner / 'ACK', sha((owner / 'manifest.json').read_bytes()).encode())
    except BaseException:
        with (owner / 'FAILED').open('xb') as stream:
            stream.write(b'Publication failed; never reopen as accepted.\n')
        raise
    return owner, result


def verify_row(index, row, plan, prepared, cfg, saved):
    _, _, papers, gold, nodes, _, requests, errors, baselines, recordings, _ = prepared
    unit, key, node, arm = plan
    Result.model_validate(row)
    base = base_row(unit, papers[key], node, arm, gold)
    name = f'terminal-{index}.bin'
    if ('terminal' in row) != (name in saved) or ('terminal' in row and row['terminal'] != name):
        raise ValueError('terminal ownership/reference mismatch')
    if key in errors:
        base['status'] = errors[key]
    elif arm.kind == 'similarity':
        baseline_result(base, arm, baselines[arm.arm_id].get(key), nodes)
    elif 'terminal' not in row:
        check_unattempted(row, base, arm, requests[unit], recordings.get((unit, arm.arm_id)))
        return
    else:
        if arm.blockage():
            raise ValueError('blocked arm cannot own dispatched terminal')
        terminal = saved[name]
        telemetry = row['telemetry']
        if arm.kind == 'recorded':
            if terminal != recordings.get((unit, arm.arm_id)):
                raise ValueError('recording input/terminal mismatch')
            expected = {'reserved': False, 'dispatched': False, 'terminal_status': 'received',
                        'elapsed_seconds': None, 'client_retries': 0}
            if telemetry != expected:
                raise ValueError('recorded transport telemetry mismatch')
        elif telemetry.get('reserved') is not True or telemetry.get('dispatched') is not True:
            raise ValueError('native dispatched telemetry mismatch')
        status, readout = project(terminal, arm, cfg, telemetry['terminal_status'])
        base.update(status=status, readout=readout, telemetry=telemetry, terminal=name,
                    request_sha256=sha(requests[unit]), label=readout['label'] if status == 'decision' else None)
    if base != row:
        raise ValueError('outcome/admitted projection mismatch')


def checked_timings(raw, rows, plans, questions):
    groups = {}
    for row, (_, key, _, arm) in zip(rows, plans):
        if arm.kind == 'native':
            groups.setdefault((key, arm.arm_id), []).append(row)
    expected = {k for k, values in groups.items() if len(values) == len(questions)
                and all(r['telemetry'].get('dispatched') is True for r in values)}
    data = loads(raw)
    if not isinstance(data, list):
        raise ValueError('paper timing inventory must be an array')
    times = [PaperTiming.model_validate(t).model_dump(mode='json') for t in data]
    owners = {(t['paper_sha256'], t['arm_id']) for t in times}
    if len(owners) != len(times) or owners != expected:
        raise ValueError('paper timing ownership/completion mismatch')
    return times


def reopen(owner):
    owner = Path(owner)
    if owner.is_symlink() or any((owner / n).is_symlink() for n in ('ACK', 'manifest.json')):
        raise ValueError('symlinked publication')
    if (owner / 'FAILED').exists():
        raise ValueError('failed owner is not replayable')
    raw = read_owned(owner / 'manifest.json')
    if read_owned(owner / 'ACK', 64) != sha(raw).encode():
        raise ValueError('missing publication acknowledgement')
    manifest = loads(raw)
    if (manifest['version'] != 1 or manifest['code'] != code_identity()
            or manifest['schema'] != schema_identity() or manifest['baseline_owner'] != baseline_owner()):
        raise ValueError('code/schema/baseline source identity mismatch')
    if {p.name for p in owner.iterdir()} != set(manifest['files']) | {'manifest.json', 'ACK'}:
        raise ValueError('partial/foreign artifact inventory')
    saved = {}
    for name, expected in manifest['files'].items():
        if Path(name).name != name or (owner / name).is_symlink():
            raise ValueError('unsafe artifact path')
        value = read_owned(owner / name)
        if sha(value) != expected:
            raise ValueError('corrupt artifact')
        saved[name] = value
    cfg = Config.model_validate(loads(saved['config.json']))
    prepared = prepare(cfg, saved)
    inputs, truth, papers, _, _, questions, *_ = prepared
    plans = list(units(papers, questions, cfg))
    if manifest['rows'] != len(plans):
        raise ValueError('incomplete question/arm outcomes')
    rows = [loads(saved[f'case-{i}.json']) for i in range(len(plans))]
    for index, (row, plan) in enumerate(zip(rows, plans)):
        verify_row(index, row, plan, prepared, cfg, saved)
    expected = {'config.original.json', 'config.json', 'inputs.json', 'gold.json',
                'paper-times.json', 'report.json', 'report.md'}
    expected.update(f'baseline-{i}.json' for i, arm in enumerate(cfg.baselines) if arm.artifact)
    expected.update(f'recording-{i}.bin' for i in range(len(inputs.recordings)))
    expected.update(f'case-{i}.json' for i in range(len(plans)))
    expected.update(r['terminal'] for r in rows if 'terminal' in r)
    if set(saved) != expected:
        raise ValueError('foreign/missing admitted publication inventory')
    timings = checked_timings(saved['paper-times.json'], rows, plans, questions)
    result = report(rows, cfg, inputs, truth, timings=timings)
    if encoded(result) != saved['report.json'] or readable(result).encode() != saved['report.md']:
        raise ValueError('report projection mismatch')
    return result


def command(args, *, transport=None):
    if args.inspect:
        if args.run:
            raise ValueError('inspection never runs or writes')
        result = reopen(args.inspect)
    else:
        raw = read_owned(args.config, 1048576)
        values = loads(raw)
        overrides = loads(os.environ.get('ZS_COLLECTION_OVERRIDES', '{}').encode())
        if not isinstance(values, dict) or not isinstance(overrides, dict):
            raise ValueError('configuration/overrides must be JSON objects')
        cfg = Config.model_validate({**values, **overrides})
        safety = int(os.environ.get('ZS_SOURCE_WORK_SAFETY_MS', '3600000'))
        if not 0 < safety <= 7200000:
            raise ValueError('source wall safety must be1..7200000ms')
        cfg = Config.model_validate({**cfg.model_dump(), 'max_run_seconds': min(cfg.max_run_seconds, safety / 1000)})
        if args.run:
            owner, result = run(cfg, raw, transport=transport)
            print(f'Private artifacts: {owner}')
        else:
            inputs, truth, papers, _, _, questions, *_ = prepare(cfg)
            result = {'mode': 'inspect-inputs', 'papers': len(papers), 'questions': len(papers) * len(questions),
                      'synthetic': truth.synthetic, 'model_calls': 0, 'writes': 0}
    print(encoded(result).decode())
    return 0
