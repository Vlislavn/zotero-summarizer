from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.migrations import migrate_existing


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "zotero_summarizer.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _mcp(_: argparse.Namespace) -> int:
    from zotero_summarizer.mcp.server import main

    main()
    return 0


def _migrate(args: argparse.Namespace) -> int:
    settings = Settings.load(project_root=args.project_root)
    result = migrate_existing(settings)
    print(
        json.dumps(
            {
                "schema_version": result.schema_version,
                "triage_db_path": str(result.triage_db_path),
                "corpus_db_path": str(result.corpus_db_path),
            },
            indent=2,
        )
    )
    return 0


def _feeds_run(args: argparse.Namespace) -> int:
    """Run the RSS-feed batch processor end-to-end.

    Reads Zotero's `feedItems`, triages each on abstract, plateau-selects the
    top items, and queues pending changes (review + apply via the existing
    `/api/pending` flow). Zotero auto-fetches PDFs after the items land in the
    Inbox collection.

    The FastAPI lifespan that normally bootstraps app state (LLM client, corpus
    cache, etc.) does NOT fire on CLI invocation, so we replicate it here via
    `lifecycle.startup()` inside an asyncio event loop. Background tasks
    started by lifecycle (auto-corpus-import) are cancelled on exit — they
    resume on the next run.
    """
    import asyncio

    feed_filter: list[int] | None = None
    if args.feeds:
        try:
            feed_filter = [int(x) for x in args.feeds.split(",") if x.strip()]
        except ValueError:
            print("ERROR: --feeds must be a comma-separated list of integer library IDs", file=sys.stderr)
            return 2

    async def _run() -> int:
        settings = Settings.load(project_root=args.project_root)
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.feeds import run_feed_batch

        set_context(AppContext(settings=settings))
        lifecycle.startup()

        # run_feed_batch is synchronous + LLM-blocking; off-thread it so we
        # don't starve the asyncio loop that lifecycle's background tasks use.
        report = await asyncio.to_thread(
            run_feed_batch,
            since_days=args.since,
            feed_library_ids=feed_filter,
            dry_run=args.dry_run,
        )
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if not report.errors else 1

    return asyncio.run(_run())


def _feeds_list(args: argparse.Namespace) -> int:
    """Show every Zotero RSS feed (one feed library per row)."""
    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services.feeds import list_feed_groups

    feeds = list_feed_groups(ZoteroReader(settings.zotero_data_dir))
    if args.json:
        print(json.dumps(feeds, indent=2))
        return 0

    # Human-friendly table
    if not feeds:
        print("(no Zotero RSS feeds configured)")
        return 0
    name_w = min(60, max(len(f["name"]) for f in feeds))
    print(f"{'ID':>6}  {'NAME':<{name_w}}  LAST UPDATE")
    print("-" * (6 + 2 + name_w + 2 + 20))
    for f in feeds:
        print(f"{f['library_id']:>6}  {f['name'][:name_w]:<{name_w}}  {f['last_update']}")
    return 0


def _feeds_serve(args: argparse.Namespace) -> int:
    """Run the Phase 1.5 background daemon: continuously triage unread feeds.

    Each tick (default every 5 min):
      - picks N unread feed items round-robin across feeds
      - triages them (LLM scoring + corpus pre-filter)
      - marks them read in Zotero so the unread badge updates
      - resolves a few due outcomes from prior materializations
      - if 24h has elapsed, runs daily selection: materializes 1-2 best
        items into the Inbox collection with tags + concise note
    SIGTERM / SIGINT finishes the current tick cleanly before exiting.
    """
    import asyncio

    feed_filter: list[int] | None = None
    if args.feeds:
        try:
            feed_filter = [int(x) for x in args.feeds.split(",") if x.strip()]
        except ValueError:
            print("ERROR: --feeds must be a comma-separated list of integer library IDs", file=sys.stderr)
            return 2

    async def _run() -> int:
        settings = Settings.load(project_root=args.project_root)
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.feeds import run_daemon_loop

        set_context(AppContext(settings=settings))
        lifecycle.startup()
        await run_daemon_loop(
            feed_library_ids=feed_filter,
            max_ticks=args.max_ticks,
        )
        return 0

    return asyncio.run(_run())


def _feeds_select_daily(args: argparse.Namespace) -> int:
    """Manually trigger one round of daily selection.

    Normally the daemon runs this once every `daily_selection_interval_hours`
    (default 24h). This subcommand forces it on demand — useful for testing
    or for catching up after a long downtime.
    """
    import asyncio

    async def _run() -> int:
        settings = Settings.load(project_root=args.project_root)
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.feeds import run_daily_selection

        set_context(AppContext(settings=settings))
        lifecycle.startup()
        result = await asyncio.to_thread(run_daily_selection, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0 if not result.get("errors") else 1

    return asyncio.run(_run())


def _feeds_tick(args: argparse.Namespace) -> int:
    """Run exactly one daemon tick and exit.

    Useful for cron-driven setups (e.g., macOS launchd / systemd timer)
    instead of a long-running daemon, or for one-off testing.
    """
    import asyncio

    feed_filter: list[int] | None = None
    if args.feeds:
        try:
            feed_filter = [int(x) for x in args.feeds.split(",") if x.strip()]
        except ValueError:
            print("ERROR: --feeds must be a comma-separated list of integer library IDs", file=sys.stderr)
            return 2

    async def _run() -> int:
        settings = Settings.load(project_root=args.project_root)
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.feeds import run_daemon_tick

        set_context(AppContext(settings=settings))
        lifecycle.startup()
        report = await asyncio.to_thread(
            run_daemon_tick,
            feed_library_ids=feed_filter,
            batch_size=args.batch_size,
            force_daily_selection=args.force_daily,
        )
        print(json.dumps(report.as_dict(), indent=2))
        return 0

    return asyncio.run(_run())


def _feeds_preview(args: argparse.Namespace) -> int:
    """Peek at the most recent feed items for one feed (read-only)."""
    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services.feeds import preview_feed

    items = preview_feed(
        feed_library_id=args.feed_id,
        since_days=args.since,
        limit=args.limit,
        reader=ZoteroReader(settings.zotero_data_dir),
        unread_only=args.unread_only,
    )
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print(f"(no items in feed {args.feed_id} since the last {args.since} days)")
        return 0
    for item in items:
        title = (item.get("title") or "Untitled")[:100]
        feed_name = item.get("feed_name") or ""
        date_added = item.get("date_added") or ""
        print(f"[{date_added}] ({feed_name}) {title}")
    return 0


def _smoke_test(args: argparse.Namespace) -> int:
    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.api.app import create_app

    app = create_app(settings)
    payload = {
        "ok": True,
        "project_root": str(settings.project_root),
        "config_path": str(settings.config_path),
        "route_count": len(app.routes),
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotero-summarizer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the local FastAPI server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    mcp = subparsers.add_parser("mcp", help="Run the MCP server over stdio")
    mcp.set_defaults(func=_mcp)

    migrate = subparsers.add_parser("migrate", help="Initialize or migrate local SQLite stores")
    migrate.add_argument("--project-root", default=None)
    migrate.set_defaults(func=_migrate)

    smoke = subparsers.add_parser("smoke-test", help="Verify package import and app construction")
    smoke.add_argument("--project-root", default=None)
    smoke.set_defaults(func=_smoke_test)

    # --- feeds <run|list|preview> ------------------------------------------
    feeds = subparsers.add_parser(
        "feeds",
        help="Process Zotero RSS feeds (Phase 1: read → triage → plateau-select → queue)",
    )
    feeds_subparsers = feeds.add_subparsers(dest="feeds_command", required=True)

    feeds_run = feeds_subparsers.add_parser(
        "run",
        help="Run the feed batch end-to-end; queues pending changes for review",
    )
    feeds_run.add_argument(
        "--since",
        type=int,
        default=None,
        help="Only process items added in the last N days (default: goals.yaml feeds.default_since_days)",
    )
    feeds_run.add_argument(
        "--feeds",
        default=None,
        help="Comma-separated feed library IDs (e.g. '2,5,7'). Default: all feeds.",
    )
    feeds_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Triage + select but do NOT persist any pending changes or decisions",
    )
    feeds_run.add_argument("--project-root", default=None)
    feeds_run.set_defaults(func=_feeds_run)

    feeds_list = feeds_subparsers.add_parser("list", help="List configured Zotero RSS feeds")
    feeds_list.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    feeds_list.add_argument("--project-root", default=None)
    feeds_list.set_defaults(func=_feeds_list)

    feeds_preview = feeds_subparsers.add_parser(
        "preview",
        help="Peek at the most recent items in one feed (read-only, no LLM)",
    )
    feeds_preview.add_argument("feed_id", type=int, help="Feed library ID (use `feeds list` to find)")
    feeds_preview.add_argument("--since", type=int, default=7, help="Items added in the last N days")
    feeds_preview.add_argument("--limit", type=int, default=50)
    feeds_preview.add_argument(
        "--unread-only",
        action="store_true",
        help="Only show items where feedItems.readTime IS NULL",
    )
    feeds_preview.add_argument("--json", action="store_true")
    feeds_preview.add_argument("--project-root", default=None)
    feeds_preview.set_defaults(func=_feeds_preview)

    # Phase 1.5 daemon — the primary user workflow.
    feeds_serve = feeds_subparsers.add_parser(
        "serve",
        help="Run the Phase 1.5 background daemon (continuous lazy triage)",
    )
    feeds_serve.add_argument(
        "--feeds",
        default=None,
        help="Comma-separated feed library IDs (default: all feeds)",
    )
    feeds_serve.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="Exit after N ticks (default: run forever; useful for testing)",
    )
    feeds_serve.add_argument("--project-root", default=None)
    feeds_serve.set_defaults(func=_feeds_serve)

    feeds_tick = feeds_subparsers.add_parser(
        "tick",
        help="Run exactly one daemon tick and exit (cron-friendly)",
    )
    feeds_tick.add_argument(
        "--feeds",
        default=None,
        help="Comma-separated feed library IDs (default: all feeds)",
    )
    feeds_tick.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Items to process this tick (default: goals.yaml feeds.daemon_batch_size)",
    )
    feeds_tick.add_argument(
        "--force-daily",
        action="store_true",
        help="Force daily selection to run this tick (skip the 24h interval check)",
    )
    feeds_tick.add_argument("--project-root", default=None)
    feeds_tick.set_defaults(func=_feeds_tick)

    feeds_daily = feeds_subparsers.add_parser(
        "select-daily",
        help="Manually trigger daily selection over the rolling 24h triage pool",
    )
    feeds_daily.add_argument(
        "--dry-run",
        action="store_true",
        help="Plateau-select but don't materialize items into Zotero",
    )
    feeds_daily.add_argument("--project-root", default=None)
    feeds_daily.set_defaults(func=_feeds_select_daily)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
