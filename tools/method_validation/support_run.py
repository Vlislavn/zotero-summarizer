"""Admission -> serial comparison -> exclusively owned private artifacts -> verified reopen."""
from __future__ import annotations

import os
from math import isfinite
from pathlib import Path
from time import monotonic

from .decision_adapter import admit_native, evaluate_support, payload, project
from .support import (BaselineArtifact, Config, FrozenSupportInput, OWNERS, SupportResult, admit, digest,
                      encoded, gold_identity, loads, read_bound, sha)
from .support_report import readable, report


def code_identity():
    return {p.name: sha(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py'))}


def schema_identity():
    return digest([model.model_json_schema() for model in (
        Config, FrozenSupportInput, BaselineArtifact, SupportResult)])


def prepare(cfg, snapshots=None):
    saved = {} if snapshots is None else dict(snapshots)
    remaining = cfg.max_input_bytes
    def read(name, ref, cap):
        nonlocal remaining
        if snapshots is None:
            saved[name] = read_bound(ref, min(cap, remaining))
        if name not in saved:
            raise FileNotFoundError('input snapshot unavailable')
        raw = saved[name]
        if len(raw) > min(cap, remaining) or sha(raw) != ref.sha256:
            raise ValueError('snapshot input byte budget or identity mismatch')
        remaining -= len(raw)
        return raw
    bundle = FrozenSupportInput.model_validate(loads(read('inputs.json', cfg.inputs, cfg.max_input_bytes)))
    sources, cases, gold = admit(cfg, bundle)
    for case in cases.values():
        payload(case, cfg)  # Whole workload preflight, including UTF-8 byte caps.
    baseline_rows = {}
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
                        or artifact.owner_sha256 != OWNERS[baseline.kind]):
                    raise ValueError('baseline model/source owner mismatch')
                rows = {r.case_sha256: r for r in artifact.rows}
                if len(rows) != len(artifact.rows) or not rows.keys() <= cases.keys():
                    raise ValueError('duplicate/unmatched baseline input binding')
                if any((r.score is not None if baseline.kind == 'verifier' else r.verdict is not None)
                       for r in rows.values()):
                    raise ValueError('baseline capability mismatch')
        baseline_rows[baseline.arm_id] = rows
    recordings = {}
    for index, recording in enumerate(bundle.recordings):
        key = (recording.arm_id, recording.case_sha256)
        if key in recordings or recording.case_sha256 not in cases or recording.arm_id not in {
                a.arm_id for a in cfg.arms if a.kind == 'recorded'}:
            raise ValueError('unmatched/duplicate recorded native response')
        recordings[key] = read(f'recording-{index}.bin', recording.terminal, cfg.max_response_bytes)
    admit_native(cfg.arms, len(cases), cfg)
    return bundle, sources, cases, gold, baseline_rows, recordings, saved


def base_row(case, key, arm, source, gold):
    label = gold.get(gold_identity(case, source.text))
    return {'case_sha256': key, 'pair_id': case.pair_id, 'arm_id': arm.arm_id,
            'family': source.family, 'split': source.split, 'gold': label.label if label else None,
            'status': 'unavailable', 'support': None, 'readout': None, 'baseline_native': None,
            'capability': 'native_three_way' if hasattr(arm, 'eligibility') else 'binary_support',
            'telemetry': {'reserved': False, 'dispatched': False, 'elapsed_seconds': None}}


def baseline_result(row, baseline, native):
    row['baseline_native'] = native.model_dump() if native else None
    if native and (baseline.kind == 'verifier' or (native.score is not None and baseline.threshold is not None)):
        row['status'] = 'decision'
        row['support'] = (str(native.verdict or 'confirm').strip().lower() != 'reject'
                          if baseline.kind == 'verifier' else native.score >= baseline.threshold)
    return row


def publish(path, raw):
    if path.exists():
        raise FileExistsError(path)
    pending = path.with_name(path.name + '.pending')
    with pending.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)  # Only inside this newly reserved owner.


def run(cfg, raw_config, *, transport=None):
    prepared = prepare(cfg)
    bundle, sources, cases, gold, baselines, recordings, saved = prepared
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=cfg.project_root, env_path=Path(os.devnull))
    parent = settings.data_dir / 'support_comparison'
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve() != (settings.data_dir.resolve() / 'support_comparison'):
        raise ValueError('artifact parent may not be a symlink outside selected data_dir')
    owner = parent / cfg.attempt
    owner.mkdir(mode=0o700)  # No resume, reuse, overwrite or exists_ok.
    rows, inventory = [], {}
    def retain(name, raw):
        publish(owner / name, raw)
        inventory[name] = sha(raw)
    try:
        retain('config.original.json', raw_config)
        retain('config.json', encoded(cfg.model_dump(mode='json')))
        for name, raw in saved.items():
            retain(name, raw)
        started = monotonic()
        for key, case in cases.items():
            source = sources[case.paper_key]
            for arm in (*cfg.arms, *cfg.baselines):
                row = base_row(case, key, arm, source, gold)
                if arm.arm_id in baselines:
                    baseline_result(row, arm, baselines[arm.arm_id].get(key))
                else:
                    row['request_sha256'] = sha(payload(case, cfg))
                    blocked = arm.blockage()
                    row['telemetry']['reserved'] = arm.kind == 'native' and not blocked
                    recording = recordings.get((arm.arm_id, key))
                    left = cfg.max_run_seconds - (monotonic() - started)
                    state, reason = unattempted(arm, left, recording)
                    if state:
                        row.update(status=state, reason=reason)
                        row['telemetry']['remaining_seconds'] = left
                    else:
                        name = f'terminal-{len(rows)}.bin'
                        reply = evaluate_support(case, arm, cfg, lambda raw: retain(name, raw),
                            recorded=recording, transport=transport, timeout=min(left, cfg.idle_timeout_seconds))
                        row.update(status=reply.status, readout=reply.readout, telemetry=reply.telemetry,
                                   terminal=name, support=(reply.readout['label'] == 'supports'
                                       if reply.status == 'decision' else None))
                SupportResult.model_validate(row)
                retain(f'pair-{len(rows)}.json', encoded(row))
                rows.append(row)
        result = report(rows, cfg, bundle)
        retain('report.json', encoded(result))
        retain('report.md', readable(result).encode())
        manifest = {'version': 1, 'files': inventory, 'code': code_identity(), 'schema': schema_identity(),
                    'rows': len(rows), 'reserved_calls': sum(a.kind == 'native' and not a.blockage()
                        for a in cfg.arms) * len(cases)}
        retain('manifest.json', encoded(manifest))
        publish(owner / 'ACK', sha((owner / 'manifest.json').read_bytes()).encode())
    except BaseException:
        with (owner / 'FAILED').open('xb') as stream:
            stream.write(b'Publication failed; never reopen as accepted.\n')
        raise
    return owner, result


def unattempted(arm, remaining, recording):
    if type(remaining) not in (int, float) or not isfinite(remaining):
        raise ValueError('missing/invalid remaining-budget observation')
    blocked = arm.blockage()
    status = 'blocked' if blocked else ('wall_budget' if remaining <= 0 else
             'unavailable' if arm.kind == 'recorded' and recording is None else None)
    return status, blocked


def check_unattempted(row, base, arm, request, recording):
    remaining = row['telemetry'].get('remaining_seconds')
    status, reason = unattempted(arm, remaining, recording)
    base.update(status=status, reason=reason, request_sha256=sha(request))
    base['telemetry'].update(remaining_seconds=remaining, reserved=arm.kind == 'native' and not arm.blockage())
    if status is None or row != base:
        raise ValueError('unattempted outcome disagrees with admission/budget evidence')


def read_owned(path, cap=67108864):
    with path.open('rb') as stream:
        raw = stream.read(cap + 1)
    if len(raw) > cap:
        raise ValueError('artifact exceeds reopening byte cap')
    return raw


def reopen(owner):
    owner = Path(owner)
    if owner.is_symlink() or any((owner / n).is_symlink() for n in ('manifest.json', 'ACK')):
        raise ValueError('symlinked artifact owner/publication is not replayable')
    if (owner / 'FAILED').exists():
        raise ValueError('failed owner is not replayable')
    manifest_raw = read_owned(owner / 'manifest.json')
    if read_owned(owner / 'ACK', 64) != sha(manifest_raw).encode():
        raise ValueError('missing publication acknowledgement')
    manifest = loads(manifest_raw)
    if manifest['version'] != 1 or manifest['code'] != code_identity() or manifest['schema'] != schema_identity():
        raise ValueError('artifact code/schema identity mismatch')
    expected = set(manifest['files']) | {'manifest.json', 'ACK'}
    if {p.name for p in owner.iterdir()} != expected:
        raise ValueError('partial/foreign artifact inventory')
    saved = {}
    for name, expected_sha in manifest['files'].items():
        if Path(name).name != name or (owner / name).is_symlink():
            raise ValueError('unsafe artifact path')
        raw = read_owned(owner / name)
        if sha(raw) != expected_sha:
            raise ValueError('corrupt artifact')
        saved[name] = raw
    cfg = Config.model_validate(loads(saved['config.json']))
    bundle, sources, cases, gold, baselines, recordings, _ = prepare(cfg, saved)
    rows = [loads(saved[f'pair-{index}.json']) for index in range(manifest['rows'])]
    expected_pairs = [(key, arm) for key in cases for arm in (*cfg.arms, *cfg.baselines)]
    if len(rows) != len(expected_pairs):
        raise ValueError('incomplete pair/arm outcomes')
    for index, (row, (key, arm)) in enumerate(zip(rows, expected_pairs)):
        SupportResult.model_validate(row)
        name = f'terminal-{index}.bin'
        if ('terminal' in row) != (name in saved) or ('terminal' in row and row['terminal'] != name):
            raise ValueError('terminal ownership/reference mismatch')
        base = base_row(cases[key], key, arm, sources[cases[key].paper_key], gold)
        if any(row[k] != base[k] for k in ('case_sha256', 'pair_id', 'arm_id', 'family', 'split', 'gold', 'capability')):
            raise ValueError('pair identity mismatch')
        if arm.arm_id in baselines:
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
            if (status != row['status'] or readout != row['readout'] or row['request_sha256'] != sha(payload(cases[key], cfg))
                    or row['support'] != (readout['label'] == 'supports' if status == 'decision' else None)):
                raise ValueError('native projection mismatch')
        else:
            check_unattempted(row, base, arm, payload(cases[key], cfg), recordings.get((arm.arm_id, key)))
    if {n for n in saved if n.startswith('terminal-')} != {r['terminal'] for r in rows if 'terminal' in r}:
        raise ValueError('unreferenced terminal inventory')
    result = report(rows, cfg, bundle)
    if encoded(result) != saved['report.json'] or readable(result).encode() != saved['report.md']:
        raise ValueError('report projection mismatch')
    return result
