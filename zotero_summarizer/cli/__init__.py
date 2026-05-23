"""``zotero-summarizer`` CLI. Commands live in per-group modules; this builds
the parser by letting each module register its subcommands. See README.md."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from zotero_summarizer.cli._app import register_app
from zotero_summarizer.cli._feeds import register_feeds
from zotero_summarizer.cli._goldenset import register_goldenset
from zotero_summarizer.cli._helpers import _resolve_feed_ids  # noqa: F401  (re-export for tests)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotero-summarizer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_app(subparsers)
    register_feeds(subparsers)
    register_goldenset(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
