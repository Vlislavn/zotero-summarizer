"""Opt-in #37 CLI; default is read-only admission, not a model run."""
import argparse
import os
from pathlib import Path

from .support import Config, encoded, loads
from .support_run import prepare, reopen, run


def main(argv=None, *, transport=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    support = commands.add_parser('support')
    source = support.add_mutually_exclusive_group(required=True)
    source.add_argument('--config', type=Path)
    source.add_argument('--inspect', type=Path)
    support.add_argument('--run', action='store_true', help='consent to private writes and configured HTTP')
    args = parser.parse_args(argv)
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
                      'model_calls': 0, 'writes': 0}
    print(encoded(result).decode())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
