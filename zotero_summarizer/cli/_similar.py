"""``similar "<query>"``: the app's "Meaning" search over the library, without the server.

Builds the minimal runtime the server builds (config, embedding cache, Zotero reader) and calls the
same service the library page calls (``reading_queue.build_reading_queue(semantic=True,
include_read=True)``), so the results equal the app's. No server, no gate, no background workers; the
only wait is the cross-encoder load (``_RERANKER_WAIT_SECONDS``; fusion order when it is not ready).
The process ends with ``os._exit`` because a model-load thread may outlive the answer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections.abc import Callable
from typing import Any, NoReturn

_RERANKER_WAIT_SECONDS = 20.0
_TOTAL_SECONDS = 55.0
_WHY_CHARS = 160


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _bootstrap() -> None:
    """The runtime pieces the search path reads; never writes data (the server's startup also upserts goals)."""
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.models import AppState
    from zotero_summarizer.services._common import settings, state
    from zotero_summarizer.services.config_io import read_config
    from zotero_summarizer.services.model.reranker import get_reranker
    from zotero_summarizer.storage.corpus import EmbeddingCache

    current = settings()
    config = read_config(current.config_path, current.calibration_path)
    runtime = state()
    runtime.app_state = AppState(config=config)
    if not config.corpus.enabled:
        raise RuntimeError("the corpus is off in goals.yaml (corpus.enabled): no meaning search")
    runtime.embedding_cache = EmbeddingCache(current.corpus_db_path, config.corpus.embedding_model)
    runtime.zotero_reader = ZoteroReader(current.zotero_data_dir)
    if config.corpus.reranker_enabled:
        get_reranker(config.corpus.reranker_model).wait_until_ready(timeout=_RERANKER_WAIT_SECONDS)


def _search(query: str, limit: int) -> dict[str, Any]:
    from zotero_summarizer.services._common import state
    from zotero_summarizer.services.library import reading_queue

    result = reading_queue.build_reading_queue(
        search=query, semantic=True, include_read=True, limit=limit, include_abstract=True,
    )
    reader = state().zotero_reader
    return {**result, "stale_since": getattr(reader, "stale_since", None)}


def _first_author(authors: Any) -> str:
    if isinstance(authors, (list, tuple)):
        authors = authors[0] if authors else ""
    return str(authors or "").split(";")[0].strip()


def _line(rank: int, item: dict[str, Any]) -> str:
    why = " ".join(str(item.get("why_reason") or "").split())[:_WHY_CHARS]
    fields = (
        str(rank), " ".join(str(item.get("title") or "").split()), str(item.get("year") or "-"),
        _first_author(item.get("authors")) or "-", "read" if item.get("read") else "unread", why or "-",
        f"zotero://select/library/items/{item['item_key']}",
    )
    return " · ".join(fields)


def _header(result: dict[str, Any]) -> str:
    stale = result.get("stale_since")
    loading = " (reranker still loading)" if result.get("reranker_loading") else ""
    copy = f"stale since {stale}" if stale else "fresh"
    return f"semantic=yes reranked={'yes' if result.get('reranked') else 'no'}{loading} zotero_copy={copy}"


def _run(args: argparse.Namespace) -> int:
    query = args.query.strip()
    if not query:
        return _fail("empty query")
    if args.limit < 1:
        return _fail("--limit must be at least 1")
    try:
        _bootstrap()
        result = _search(query, args.limit)
    except Exception as exc:  # noqa: BLE001 - one clear line for the agent that ran the command
        return _fail(f"{type(exc).__name__}: {exc}")
    items = result.get("items") or []
    if not result.get("semantic") or not items:
        return _fail("no meaning-search results (corpus off or no candidates for this query)")
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    print(_header(result))
    for rank, item in enumerate(items, 1):
        print(_line(rank, item))
    return 0


def _run_with_watchdog(run: Callable[[argparse.Namespace], int], args: argparse.Namespace, seconds: float) -> NoReturn:
    """Exit without waiting for a background model-load thread."""
    def timeout() -> None:
        print(f"error: no answer within {seconds:.0f} s", file=sys.stderr, flush=True)
        os._exit(3)

    watchdog = threading.Timer(seconds, timeout)
    watchdog.daemon = True
    watchdog.start()
    code = run(args)
    watchdog.cancel()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _command(args: argparse.Namespace) -> NoReturn:
    _run_with_watchdog(_run, args, _TOTAL_SECONDS)


def register_similar(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("similar", help="Meaning search over the library, without the server")
    parser.add_argument("query", help="Topic or title words")
    parser.add_argument("--limit", type=int, default=10, help="Most papers to print (default 10)")
    parser.add_argument("--json", action="store_true", help="Print the items as JSON")
    parser.set_defaults(func=_command)
