"""``zotero-summarizer`` CLI. Commands live in per-group modules; this builds
the parser by letting each module register its subcommands. See README.md."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence


def apply_offline_env() -> bool:
    """Enable offline model loading (cache-only HuggingFace) when requested.

    Set ``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE`` so every model load
    (SPECTER2 gate, corpus embeddings, reranker) is cache-only + instant — no
    HuggingFace-hub round-trip that, on a disconnected machine, would hang for
    ~10–30 s before falling back to cache. Triggered by ``ZS_OFFLINE`` (shell or
    .env) or a pre-set ``HF_HUB_OFFLINE=1``.

    MUST run before any ``transformers``/``sentence_transformers`` import, so it
    is invoked at CLI module load (above the heavy submodule imports below) and
    returns whether offline mode is on. Reads .env (shell vars win)."""
    from dotenv import load_dotenv
    from zotero_summarizer.settings import default_project_root

    env = default_project_root() / ".env"
    if env.exists():
        load_dotenv(env, override=False)
    val = (os.getenv("ZS_OFFLINE") or "").strip().lower()
    offline = val in ("1", "true", "yes", "on") or (os.getenv("HF_HUB_OFFLINE") or "").strip() == "1"
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return offline


# Apply BEFORE the submodule imports below pull in transformers (transitively).
apply_offline_env()

from zotero_summarizer.cli._app import register_app  # noqa: E402
from zotero_summarizer.cli._feeds import register_feeds  # noqa: E402
from zotero_summarizer.cli._goldenset import register_goldenset  # noqa: E402
from zotero_summarizer.cli._helpers import _resolve_feed_ids  # noqa: F401,E402  (re-export for tests)


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
