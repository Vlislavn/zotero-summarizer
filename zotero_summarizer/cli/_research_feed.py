"""CLI entry point for the bounded weekly Research Intelligence digest."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from datetime import datetime
import sys
from pathlib import Path


def _date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO date/time") from exc


def _run(args: argparse.Namespace) -> int:
    if args.source == "telegram":
        return _run_telegram(args)
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services._common import settings as current_settings
    from zotero_summarizer.services.research_feed import run_weekly

    settings = current_settings()
    with redirect_stdout(sys.stderr):
        if not args.cached_only:
            lifecycle.startup(background=False)
        result = run_weekly(
            settings, start=args.start, end=args.end, venue=args.venue,
            shortlist_budget=args.shortlist_budget, card_budget=args.card_budget,
            dry_run=not args.queue_zotero, queue_zotero=args.queue_zotero,
            generate_reviews=not args.cached_only, review_timeout_seconds=args.review_timeout,
        )
    print(json.dumps(result, indent=2))
    return 1 if result.get("failed", 0) else 0


def _run_telegram(args: argparse.Namespace) -> int:
    from zotero_summarizer.services._common import settings as current_settings
    from zotero_summarizer.services.research_feed.telegram import run_saved

    settings = current_settings()
    live = None
    inputs = Path(args.input_jsonl) if args.input_jsonl else None
    if not args.cached_only and inputs is None:
        from zotero_summarizer.cli._telegram import run_child

        receipt = run_child(settings, "collect")
        if args.collect_only:
            print(json.dumps(receipt["export"], indent=2))
            return 0
        inputs = Path(receipt["export"]["inputs_path"])
        live = receipt["result"]
    result = run_saved(
        settings, input_jsonl=inputs, collect_only=args.collect_only,
        cached_only=args.cached_only, shortlist_budget=args.shortlist_budget,
        card_budget=args.card_budget, live_collection=live,
    )
    print(json.dumps(result, indent=2))
    return 0


def _view(args: argparse.Namespace) -> int:
    path = Path(args.report).expanduser()
    if path.suffix != ".md":
        raise ValueError("view requires a saved Markdown report")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _telegram_auth(args: argparse.Namespace) -> int:
    from zotero_summarizer.cli._telegram import authenticate

    return authenticate(args)


def _telegram_child(args: argparse.Namespace) -> int:
    from zotero_summarizer.cli._telegram import child

    return child(args)


def _validate_research_feed_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.research_feed_command in {"telegram-auth", "_telegram-child"}:
        if args.research_feed_command == "telegram-auth" or args.purpose == "auth":
            from zotero_summarizer.cli._telegram import require_terminal

            try:
                require_terminal()
            except ValueError as error:
                parser.error(str(error))
        return
    if args.research_feed_command == "view":
        return
    from zotero_summarizer.models.research_feed import parse_run_budgets

    if args.source == "app_rss":
        if args.start is None or args.end is None:
            parser.error("RSS research-feed run requires --from and --to")
        if args.input_jsonl or args.collect_only:
            parser.error("--input-jsonl and --collect-only require --source telegram")
    else:
        if args.queue_zotero or args.venue:
            parser.error("Telegram news does not support --queue-zotero or --venue")
        if args.collect_only and args.cached_only:
            parser.error("--collect-only and --cached-only are mutually exclusive")
        if args.cached_only and args.input_jsonl:
            parser.error("--cached-only cannot import new input")
    try:
        args.shortlist_budget, args.card_budget, _, args.review_timeout = parse_run_budgets(
            args.shortlist_budget, args.card_budget, 1000, args.review_timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))


def register_research_feed(subparsers: argparse._SubParsersAction) -> None:
    command = subparsers.add_parser(
        "research-feed", help="Build a bounded weekly project-specific research digest",
    )
    actions = command.add_subparsers(dest="research_feed_command", required=True)
    run = actions.add_parser("run", help="Generate canonical JSON and Markdown")
    run.add_argument("--from", dest="start", type=_date)
    run.add_argument("--to", dest="end", type=_date)
    run.add_argument("--source", choices=("app_rss", "telegram"), default="app_rss")
    run.add_argument("--input-jsonl", help="Saved news originals; processing calls the default LLM")
    run.add_argument("--collect-only", action="store_true", help="Import/export news without model work")
    run.add_argument("--venue", default="", help="Optional conference/feed-name filter")
    run.add_argument("--shortlist-budget", type=int, default=None)
    run.add_argument("--card-budget", type=int, default=None)
    run.add_argument("--review-timeout", type=int, default=3600)
    run.add_argument("--cached-only", action="store_true", help="Project current source-qualified reviews only; no model work")
    run.add_argument(
        "--queue-zotero", action="store_true",
        help="Queue idempotent tags for already materialized items (default: dry-run)",
    )
    run.add_argument("--project-root", default=None)
    run.set_defaults(func=_run)
    view = actions.add_parser("view", help="Print a retained Markdown report without processing")
    view.add_argument("--report", required=True)
    view.add_argument("--project-root", default=None)
    view.set_defaults(func=_view)
    auth = actions.add_parser("telegram-auth", help="One-time interactive Telegram authorization")
    auth.add_argument("--project-root", default=None)
    auth.set_defaults(func=_telegram_auth)
    child = actions.add_parser("_telegram-child", help=argparse.SUPPRESS)
    child.add_argument("--purpose", choices=("auth", "collect"), required=True)
    child.add_argument("--receipt", required=True)
    child.add_argument("--project-root", required=True)
    child.set_defaults(func=_telegram_child)


__all__ = ["register_research_feed"]
