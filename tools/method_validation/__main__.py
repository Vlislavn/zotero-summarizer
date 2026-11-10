"""Opt-in frozen support, priority and collection comparisons.

Default is read-only admission; --run consents to private writes and configured HTTP.
Usage: python -m tools.method_validation {support,priority,collections}
       --config /absolute/config.json [--run]
"""
import argparse
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .support import Config, encoded, loads
from .support_run import prepare, reopen, run


def main(argv=None, *, transport=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('support', 'priority', 'collections'):
        task = commands.add_parser(name)
        source = task.add_mutually_exclusive_group(required=True)
        source.add_argument('--config', type=Path)
        source.add_argument('--inspect', type=Path)
        task.add_argument('--run', action='store_true', help='consent to private writes and configured HTTP')
    args = parser.parse_args(argv)
    if args.command == 'collections':
        from .collections_run import command
        return command(args, transport=transport)
    if args.command == 'priority':
        from .priority_run import command
        return command(args, transport=transport)
    if args.inspect:
        if args.run:
            parser.error('--inspect never runs or writes')
        result = reopen(args.inspect)
    else:
        with args.config.open('rb') as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            parser.error('config exceeds 1 MiB')
        values = loads(raw)
        overrides = loads(os.environ.get('ZS_SUPPORT_OVERRIDES', '{}').encode())
        if not isinstance(values, dict) or not isinstance(overrides, dict):
            raise ValueError('config and environment overrides must be JSON objects')
        values.update(overrides)
        cfg = Config.model_validate(values)
        safety = int(os.environ.get('ZS_SOURCE_WORK_SAFETY_MS', '3600000'))
        if not 0 < safety <= 7200000:
            parser.error('source wall safety must be 1..7200000 ms')
        cfg = Config.model_validate({**cfg.model_dump(), 'max_run_seconds': min(cfg.max_run_seconds, safety / 1000)})
        if args.run:
            owner, result = run(cfg, raw, transport=transport)
            print(f'Private artifacts: {owner}')
        else:
            bundle, _, cases, *_ = prepare(cfg)
            result = {'mode': 'inspect-inputs', 'pairs': len(cases), 'synthetic': bundle.synthetic,
                      'model_calls': 0, 'writes': 0,
                      'arms': [{'arm_id': arm.arm_id, 'kind': arm.kind,
                                'blocked_reason': arm.blockage()} for arm in cfg.arms],
                      'limits': {name: getattr(cfg, name) for name in (
                          'max_pairs', 'max_input_bytes', 'max_prompt_chars', 'max_request_bytes',
                          'max_response_bytes', 'max_calls', 'max_work_units',
                          'idle_timeout_seconds', 'max_run_seconds')}}
    print(encoded(result).decode())
    return 0


def entry(argv=None):
    """Console boundary: bad input paths/configs end as one line + exit 2, never a traceback."""
    try:
        return main(argv)
    except ValidationError as error:
        detail = '; '.join(f"{'.'.join(map(str, e['loc'])) or 'config'}: {e['msg']}" for e in error.errors())
        message = f'invalid configuration: {detail}'
    except (OSError, ValueError) as error:
        message = f'{type(error).__name__}: {error}'
    print(f'error: {message}\nhint: see tools/method_validation/README.md; runnable synthetic '
          'examples: python -m tools.method_validation.examples OUT_DIR', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(entry())
