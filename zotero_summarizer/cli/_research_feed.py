"""CLI entry point for the bounded weekly Research Intelligence digest."""
from __future__ import annotations

import argparse
import json
from datetime import datetime


def _date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO date/time") from exc


def _run(args: argparse.Namespace) -> int:
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services.research_feed import run_weekly
    from zotero_summarizer.settings import Settings

    settings = Settings.load(project_root=args.project_root)
    set_context(AppContext(settings=settings))
    lifecycle.startup()
    result = run_weekly(
        settings, start=args.start, end=args.end, venue=args.venue,
        shortlist_budget=args.shortlist_budget, card_budget=args.card_budget,
        dry_run=not args.queue_zotero, queue_zotero=args.queue_zotero,
        generate_reviews=not args.cached_only, review_timeout_seconds=args.review_timeout,
    )
    print(json.dumps(result, indent=2))
    return 0


def register_research_feed(subparsers: argparse._SubParsersAction) -> None:
    command = subparsers.add_parser(
        "research-feed", help="Build a bounded weekly project-specific research digest",
    )
    actions = command.add_subparsers(dest="research_feed_command", required=True)
    run = actions.add_parser("run", help="Generate canonical JSON and Markdown")
    run.add_argument("--from", dest="start", type=_date, required=True)
    run.add_argument("--to", dest="end", type=_date, required=True)
    run.add_argument("--venue", default="", help="Optional conference/feed-name filter")
    run.add_argument("--shortlist-budget", type=int, default=None)
    run.add_argument("--card-budget", type=int, default=None)
    run.add_argument("--review-timeout", type=int, default=3600)
    run.add_argument("--cached-only", action="store_true", help="Do not generate missing deep reviews")
    run.add_argument(
        "--queue-zotero", action="store_true",
        help="Queue idempotent tags for already materialized items (default: dry-run)",
    )
    run.add_argument("--project-root", default=None)
    run.set_defaults(func=_run)


__all__ = ["register_research_feed"]
