from __future__ import annotations

import argparse
import json
import sys

from zotero_summarizer.settings import Settings
from zotero_summarizer.cli._helpers import _feeds_lock, _resolve_feed_ids


def _feeds_run(args: argparse.Namespace) -> int:
    """Process one or more feeds to completion in a single pass.

    Phase 1.14+: review mode is the only mode. Triages every unread item
    but leaves them in ``awaiting_review`` — nothing is written to Zotero
    until the user approves via ``http://localhost:8000/review`` (open with
    ``zotero-summarizer serve``).

    Use `feeds list` to discover feed names / IDs, then:
        zotero-summarizer feeds run --feeds "Agents"
        zotero-summarizer feeds run --feeds 2
    """
    import asyncio

    settings = Settings.load(project_root=args.project_root)
    feed_filter: list[int] | None = None
    if args.feeds:
        feed_filter = _resolve_feed_ids(args.feeds, settings)

    gate_only = bool(args.gate_only)

    async def _run() -> int:
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.triage.feeds import run_daemon_tick

        set_context(AppContext(settings=settings))
        lifecycle.startup(override_model=args.model or None)

        with _feeds_lock(settings.project_root):
            report = await asyncio.to_thread(
                run_daemon_tick,
                feed_library_ids=feed_filter,
                batch_size=None,                # unlimited — exhaust the feed
                force_daily_selection=False,    # never auto-materialise from `feeds run`
                review_mode=True,
                gate_only=gate_only,
                dry_run=args.dry_run,
            )
        print(json.dumps(report.as_dict(), indent=2))
        mode_hint = "gate-only" if gate_only else "LLM-triage"
        print(
            f"\n[{mode_hint}] {report.triaged} item(s) awaiting review. "
            f"Open http://localhost:8000/review (run `zotero-summarizer serve` first).",
            file=sys.stderr,
        )
        return 0

    return asyncio.run(_run())


def _feeds_list(args: argparse.Namespace) -> int:
    """Show every Zotero RSS feed (one feed library per row)."""
    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services.triage.feeds import list_feed_groups

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
    """Run the background daemon: continuously triage unread feeds.

    Each tick (default every 5 min):
      - picks N unread feed items round-robin across feeds
      - triages them (LLM scoring + corpus pre-filter)
      - marks them read in Zotero so the unread badge updates
      - resolves a few due outcomes from prior materializations
      - at the configured morning time (daily_selection_at), materializes
        the 1-2 best items from the rolling 24h pool into the Inbox collection
    SIGTERM / SIGINT finishes the current tick cleanly before exiting.
    """
    import asyncio

    settings = Settings.load(project_root=args.project_root)
    feed_filter: list[int] | None = None
    if args.feeds:
        feed_filter = _resolve_feed_ids(args.feeds, settings)

    async def _run() -> int:
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.triage.feeds import run_daemon_loop

        set_context(AppContext(settings=settings))
        lifecycle.startup(override_model=args.model or None)

        with _feeds_lock(settings.project_root):
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
        from zotero_summarizer.services.triage.feeds import run_daily_selection

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
    Does NOT acquire the PID lock — intentionally safe to run alongside the daemon.
    """
    import asyncio

    settings = Settings.load(project_root=args.project_root)
    feed_filter: list[int] | None = None
    if args.feeds:
        feed_filter = _resolve_feed_ids(args.feeds, settings)

    async def _run() -> int:
        from zotero_summarizer.runtime import AppContext, set_context
        from zotero_summarizer.services import lifecycle
        from zotero_summarizer.services.triage.feeds import run_daemon_tick

        set_context(AppContext(settings=settings))
        lifecycle.startup(override_model=args.model or None)
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
    from zotero_summarizer.services.triage.feeds import preview_feed

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



def register_feeds(subparsers) -> None:
    # --- feeds <run|list|preview> ------------------------------------------
    feeds = subparsers.add_parser(
        "feeds",
        help="Process Zotero RSS feeds (Phase 1: read → triage → plateau-select → queue)",
    )
    feeds_subparsers = feeds.add_subparsers(dest="feeds_command", required=True)

    feeds_run = feeds_subparsers.add_parser(
        "run",
        help=(
            "Process one feed to completion in a single pass. "
            "Triages every unread item and parks it as `awaiting_review`; "
            "approval happens in the review UI."
        ),
    )
    feeds_run.add_argument(
        "--feeds",
        default=None,
        help=(
            "Feed name substring or numeric ID (use `feeds list` to discover). "
            "Accepts comma-separated values, e.g. 'Agents' or '2,5'. Default: all feeds."
        ),
    )
    feeds_run.add_argument(
        "--model",
        default=None,
        help="Override LLM model for this run (e.g. qwen3:8b). Uses goals.yaml value if omitted.",
    )
    feeds_run.add_argument(
        "--gate-only",
        action="store_true",
        help=(
            "Phase 1.14: skip the LLM entirely. Each survivor of the classifier "
            "gate is synthesised as an awaiting_review row using the gate's "
            "predicted priority + SHAP attribution + OpenAlex author/venue. "
            "Use this to bootstrap golden-CSV labels through the UI without "
            "paying for LLM calls on every item."
        ),
    )
    feeds_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Triage + select but do NOT persist any decisions or materialize items",
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
        help="Run the background daemon (continuous lazy triage, 5 items/5 min)",
    )
    feeds_serve.add_argument(
        "--feeds",
        default=None,
        help=(
            "Feed name substring or numeric ID (use `feeds list` to discover). "
            "Comma-separated. Default: all feeds."
        ),
    )
    feeds_serve.add_argument(
        "--model",
        default=None,
        help="Override LLM model for this daemon session. Uses goals.yaml value if omitted.",
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
        help="Run exactly one daemon tick and exit (cron-friendly; safe alongside daemon)",
    )
    feeds_tick.add_argument(
        "--feeds",
        default=None,
        help=(
            "Feed name substring or numeric ID. "
            "Comma-separated. Default: all feeds."
        ),
    )
    feeds_tick.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Items to process this tick (default: goals.yaml feeds.daemon_batch_size)",
    )
    feeds_tick.add_argument(
        "--model",
        default=None,
        help="Override LLM model for this tick. Uses goals.yaml value if omitted.",
    )
    feeds_tick.add_argument(
        "--force-daily",
        action="store_true",
        help="Force daily selection to run this tick (skip the time-of-day check)",
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

