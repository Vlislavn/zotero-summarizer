"""Explicit priority comparison, exclusive local artifacts and effect-free verified replay."""
import os
from pathlib import Path
from time import monotonic

from .decision_adapter import NativeDecisionAdapter, ProviderReply, admit_native
from .priority import (BaselineArtifact, BaselineReadout, Config, Inputs, Readout, Result,
                       Truth, admit, payload, project)
from .priority_report import readable, report
from .support import digest, encoded, loads, read_bound, sha
from .support_run import check_unattempted, code_identity, publish, read_owned, unattempted


def schema_identity():
    return digest([x.model_json_schema() for x in
                   (Config, Inputs, Truth, BaselineArtifact, BaselineReadout, Readout, Result)])


def baseline_owners():
    root = Path(__file__).resolve().parents[2] / 'zotero_summarizer/services/model'
    return {kind: sha((root / name).read_bytes()) for kind, name in
            (('ml', 'classifier.py'), ('llm', 'llm_classifier.py'))}


def prepare(cfg, snapshots=None):
    saved = {} if snapshots is None else dict(snapshots)
    admitted_bytes = 0
    def read(name, ref, cap):
        nonlocal admitted_bytes
        if snapshots is None:
            saved[name] = read_bound(ref, min(cap, cfg.max_snapshot_bytes - admitted_bytes))
        if name not in saved:
            raise FileNotFoundError('frozen snapshot unavailable')
        raw = saved[name]
        if len(raw) > cap or sha(raw) != ref.sha256:
            raise ValueError('snapshot byte/hash identity mismatch')
        admitted_bytes += len(raw)
        if admitted_bytes > cfg.max_snapshot_bytes:
            raise ValueError('cumulative snapshot byte budget exceeded')
        return raw
    inputs = Inputs.model_validate(loads(read('inputs.json', cfg.inputs, cfg.max_input_bytes)))
    truth = Truth.model_validate(loads(read('gold.json', cfg.gold, cfg.max_input_bytes)))
    papers, gold = admit(cfg, inputs, truth)
    row_work = len(papers) * (len(cfg.arms) + len(cfg.baselines))
    if row_work > cfg.max_work_units:
        raise ValueError('whole-workload outcome budget exceeded')
    requests, errors = {}, {}
    for key, paper in papers.items():
        try:
            requests[key] = payload(paper, cfg)
        except ValueError as error:
            if error.args[0] not in ('missing_input', 'input_too_large'):
                raise
            errors[key] = error.args[0]
    baselines = {}
    held = [p for p in papers.values() if p.split == 'holdout']
    owners = baseline_owners()
    for index, baseline in enumerate(cfg.baselines):
        rows = {}
        if baseline.artifact:
            try:
                raw = read(f'baseline-{index}.json', baseline.artifact, cfg.max_input_bytes)
            except FileNotFoundError:
                raw = None
            if raw is not None:
                artifact = BaselineArtifact.model_validate(loads(raw))
                if (artifact.kind != baseline.kind or artifact.model != baseline.model
                        or artifact.owner_sha256 != owners[baseline.kind]):
                    raise ValueError('baseline model/source binding mismatch')
                if baseline.kind == 'ml' and (not artifact.trained_at or not artifact.training_families):
                    raise ValueError('ML training provenance is required, never infer holdout safety')
                if held and baseline.kind == 'ml' and (artifact.trained_at >= min(p.observed_at for p in held)
                        or set(artifact.training_families) & {p.family for p in held}):
                    raise ValueError('trained baseline holdout leakage')
                rows = {r.get('case_sha256'): r for r in artifact.rows}
                if len(rows) != len(artifact.rows) or not rows.keys() <= papers.keys():
                    raise ValueError('duplicate/unmatched normalized baseline identity')
        baselines[baseline.arm_id] = rows
    recordings = {}
    for index, recording in enumerate(inputs.recordings):
        key = (recording.arm_id, recording.case_sha256)
        if key in recordings or recording.case_sha256 not in papers or recording.arm_id not in {
                a.arm_id for a in cfg.arms if a.kind == 'recorded'}:
            raise ValueError('duplicate/unmatched recorded response')
        recordings[key] = read(f'recording-{index}.bin', recording.terminal, cfg.max_response_bytes)
    admit_native(cfg.arms, len(requests), cfg)
    work = row_work + sum(a.forward_passes_bound for a in cfg.arms
                         if a.kind == 'native' and not a.blockage()) * len(requests)
    if work > cfg.max_work_units:
        raise ValueError('whole-workload call/work reservation exceeds budget')
    return inputs, truth, papers, gold, requests, errors, baselines, recordings, saved


def base_row(key, paper, arm, gold):
    target = gold.get(key)
    capability = {'native': 'native_four_way', 'recorded': 'native_four_way',
                  'blocked': 'native_four_way', 'ml': 'ml_regression', 'llm': 'llm_ordinal'}[arm.kind]
    return {'case_sha256': key, 'paper_id': paper.paper_id, 'family': paper.family,
            'split': paper.split, 'stratum': paper.stratum, 'arm_id': arm.arm_id, 'capability': capability,
            'gold': target.label if target and target.human_provenance else None,
            'status': 'unavailable', 'label': None, 'readout': None, 'baseline_native': None,
            'telemetry': {'reserved': False, 'dispatched': False, 'elapsed_seconds': None}}


def baseline_result(row, baseline, native):
    if native:
        try:
            result = BaselineReadout.model_validate(native)
        except ValueError:
            row.update(status='invalid_response', reason='invalid baseline row; original artifact retained')
            return row
        row['baseline_native'] = native
        if result.observed_model != baseline.model:
            row['status'] = 'model_mismatch'
        else:
            row.update(status='decision', label=result.label)
            row['telemetry']['elapsed_seconds'] = result.elapsed_seconds
    return row


def evaluate(row, arm, cfg, request, retain, *, recording=None, transport=None, remaining=None):
    remaining = cfg.max_run_seconds if remaining is None else remaining
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
    inputs, truth, papers, gold, requests, errors, baselines, recordings, saved = prepare(cfg)
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=cfg.project_root, env_path=Path(os.devnull))
    parent = settings.data_dir / 'priority_comparison'
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve() != settings.data_dir.resolve() / 'priority_comparison':
        raise ValueError('artifact parent may not escape selected data_dir')
    owner = parent / cfg.attempt
    owner.mkdir(mode=0o700)
    inventory, rows = {}, []
    def retain(name, raw):
        publish(owner / name, raw)
        inventory[name] = sha(raw)
    try:
        retain('config.original.json', raw_config)
        retain('config.json', encoded(cfg.model_dump(mode='json')))
        for name, raw in saved.items():
            retain(name, raw)
        started = monotonic()
        for key, paper in papers.items():
            for arm in (*cfg.arms, *cfg.baselines):
                row = base_row(key, paper, arm, gold)
                if key in errors:
                    row['status'] = errors[key]
                elif arm.arm_id in baselines:
                    baseline_result(row, arm, baselines[arm.arm_id].get(key))
                else:
                    name = f'terminal-{len(rows)}.bin'
                    def native_retain(raw):
                        retain(name, raw)
                        row['terminal'] = name
                    evaluate(row, arm, cfg, requests[key], native_retain,
                             recording=recordings.get((arm.arm_id, key)), transport=transport,
                             remaining=cfg.max_run_seconds - (monotonic() - started))
                Result.model_validate(row)
                retain(f'case-{len(rows)}.json', encoded(row))
                rows.append(row)
        result = report(rows, cfg, inputs, truth)
        retain('report.json', encoded(result))
        retain('report.md', readable(result).encode())
        manifest = {'version': 1, 'files': inventory, 'code': code_identity(), 'schema': schema_identity(),
                    'rows': len(rows), 'baseline_owners': baseline_owners()}
        retain('manifest.json', encoded(manifest))
        publish(owner / 'ACK', sha((owner / 'manifest.json').read_bytes()).encode())
    except BaseException:
        with (owner / 'FAILED').open('xb') as stream:
            stream.write(b'Publication failed; never reopen as accepted.\n')
        raise
    return owner, result


def reopen(owner):
    owner = Path(owner)
    if owner.is_symlink() or any((owner / n).is_symlink() for n in ('ACK', 'manifest.json')):
        raise ValueError('symlinked artifact publication')
    if (owner / 'FAILED').exists():
        raise ValueError('failed owner is not replayable')
    raw = read_owned(owner / 'manifest.json')
    if read_owned(owner / 'ACK', 64) != sha(raw).encode():
        raise ValueError('missing publication acknowledgement')
    manifest = loads(raw)
    if (manifest['version'] != 1 or manifest['code'] != code_identity()
            or manifest['schema'] != schema_identity() or manifest['baseline_owners'] != baseline_owners()):
        raise ValueError('artifact code/schema/baseline source identity mismatch')
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
    inputs, truth, papers, gold, requests, errors, baselines, recordings, _ = prepare(cfg, saved)
    rows = [loads(saved[f'case-{i}.json']) for i in range(manifest['rows'])]
    pairs = [(key, arm) for key in papers for arm in (*cfg.arms, *cfg.baselines)]
    if len(rows) != len(pairs):
        raise ValueError('incomplete case/arm outcomes')
    for index, (row, (key, arm)) in enumerate(zip(rows, pairs)):
        Result.model_validate(row)
        name = f'terminal-{index}.bin'
        if ('terminal' in row) != (name in saved) or ('terminal' in row and row['terminal'] != name):
            raise ValueError('terminal ownership/reference mismatch')
        base = base_row(key, papers[key], arm, gold)
        if any(row[k] != base[k] for k in
               ('case_sha256', 'paper_id', 'family', 'split', 'stratum', 'arm_id', 'capability', 'gold')):
            raise ValueError('case identity mismatch')
        if key in errors:
            base['status'] = errors[key]
            if row != base:
                raise ValueError('invalid input became a prediction')
        elif arm.arm_id in baselines:
            if row != baseline_result(base, arm, baselines[arm.arm_id].get(key)):
                raise ValueError('baseline projection mismatch')
        elif 'terminal' in row:
            if arm.blockage():
                raise ValueError('blocked arm cannot own a dispatched terminal')
            terminal = saved[row['terminal']]
            if arm.kind == 'recorded':
                recording = recordings.get((arm.arm_id, key))
                if terminal != recording or row['telemetry']['terminal_status'] != 'received':
                    raise ValueError('recording input/terminal binding mismatch')
                terminal = recording
            status, readout = project(terminal, arm, cfg, row['telemetry']['terminal_status'])
            if (status != row['status'] or readout != row['readout'] or row['request_sha256'] != sha(requests[key])
                    or row['label'] != (readout['label'] if status == 'decision' else None)):
                raise ValueError('native projection mismatch')
        else:
            check_unattempted(row, base, arm, requests[key], recordings.get((arm.arm_id, key)))
    if {n for n in saved if n.startswith('terminal-')} != {r['terminal'] for r in rows if 'terminal' in r}:
        raise ValueError('unreferenced terminal inventory')
    result = report(rows, cfg, inputs, truth)
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
        overrides = loads(os.environ.get('ZS_PRIORITY_OVERRIDES', '{}').encode())
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
            inputs, truth, papers, *_ = prepare(cfg)
            result = {'mode': 'inspect-inputs', 'papers': len(papers), 'synthetic': truth.synthetic,
                      'model_calls': 0, 'writes': 0}
    print(encoded(result).decode())
    return 0
