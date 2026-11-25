"""``candidates <note.md>``: library papers to offer for one Obsidian note, without the server.

Read-only. The note → the ``feed`` stage's local model (``local_only``: nothing leaves the
machine) writes an English query → ``similar``'s meaning search → filters in
``services/library/note_candidates`` → JSON on stdout. The Obsidian button merges the lines
into the note's «Кандидаты из Zotero» section itself, so the note is never written here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

_TOTAL_SECONDS = 150.0
_POOL = 15


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _local_client():
    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services._common import state
    from zotero_summarizer.services.llm.factory import build_client_for_provider

    resolved = resolve_stage(state().app_state.config.llm_routing, "feed")
    return build_client_for_provider(resolved.provider, resolved.model, enable_thinking=False, local_only=True)


def _run(args: argparse.Namespace) -> int:
    from zotero_summarizer.cli._similar import _bootstrap, _search
    from zotero_summarizer.integrations.llm import to_text
    from zotero_summarizer.services.library import note_candidates as nc

    started = time.monotonic()
    path = Path(args.note)
    if not path.is_file():
        return _fail(f"no such note: {path}")
    markdown = path.read_text(encoding="utf-8")
    title, text = path.stem, nc.note_text(markdown)
    try:
        _bootstrap()
        client = _local_client()
        ask = lambda prompt: to_text(client.prompt(prompt))  # noqa: E731
        query = nc.query_from_answer(ask(nc.TRANSLATE_PROMPT.format(title=title, text=text)))
        result = _search(query, _POOL)
        chosen, dropped = nc.select(result.get("items") or [], nc.known_keys(markdown),
                                    lambda item: nc.is_yes(ask(nc.useful_prompt(title, text, item))), args.limit)
    except Exception as exc:  # noqa: BLE001 - one clear line for the Obsidian notice
        return _fail(f"{type(exc).__name__}: {exc}")
    print(json.dumps({
        "query": query, "lines": [nc.link_line(item) for item in chosen],
        "keys": [item["item_key"] for item in chosen], "searched": len(result.get("items") or []),
        "dropped": dropped, "stale_since": result.get("stale_since"),
        "seconds": round(time.monotonic() - started, 1),
    }, ensure_ascii=False))
    return 0


def _timeout() -> None:
    print(f"error: no answer within {_TOTAL_SECONDS:.0f} s", file=sys.stderr, flush=True)
    os._exit(3)


def _command(args: argparse.Namespace) -> int:
    watchdog = threading.Timer(_TOTAL_SECONDS, _timeout)
    watchdog.daemon = True
    watchdog.start()
    code = _run(args)
    watchdog.cancel()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # a model-load thread may outlive the answer


def register_candidates(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("candidates", help="Library papers to offer for one Obsidian note (read-only)")
    parser.add_argument("note", help="Path to the note's .md file")
    parser.add_argument("--limit", type=int, default=10, help="Most papers to offer (default 10)")
    parser.set_defaults(func=_command)
