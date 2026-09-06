from __future__ import annotations

import argparse
import json

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.migrations import migrate_existing


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    # factory=True so the app is built when uvicorn starts (and on each reload),
    # not as an import-time side effect of api.app.
    uvicorn.run(
        "zotero_summarizer.api.app:create_app",
        factory=True,
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


def _prefetch_models(args: argparse.Namespace) -> int:
    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.services.setup.assets import asset_report, prefetch_assets

    if args.check:
        print(json.dumps(asset_report(settings), indent=2))
        return 0
    print("Prefetching local ML assets (downloads on first run)…", flush=True)
    print(json.dumps(prefetch_assets(settings), indent=2))
    return 0


def _verify_deep_review(args: argparse.Namespace) -> int:
    """Headless end-to-end deep-review check on ONE already-built paper.

    Drives the real digest + quality path against the live ``deep_review`` model
    using the paper's cached ``qa_text`` (no Zotero, no server), printing the
    per-phase timing logs (the new observability) and the resulting digest — the
    production receipt that a review actually produces a digest."""
    import logging
    import time

    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services._common import deep_review_sub_concurrency, read_config
    from zotero_summarizer.services.library import _paper_goal_summaries, quality_eval, quality_review
    from zotero_summarizer.services.library._deep_review_progress import ReviewReporter
    from zotero_summarizer.services.llm.factory import build_client_for_stage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")

    state_path = settings.data_dir / "paper_render" / args.item_key / "paper_read.json"
    if not state_path.exists():
        raise SystemExit(f"no paper_read.json for {args.item_key} at {state_path} — build the paper brief first")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    qa_text = (state.get("qa_text") or "").strip()
    title = str(state.get("title") or args.item_key)
    if not qa_text:
        raise SystemExit(f"{args.item_key} has empty qa_text — rebuild the paper brief")

    config = read_config(settings.config_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    if args.provider:
        from zotero_summarizer.models.providers import ResolvedStage
        prov = config.llm_routing.provider_by_name(args.provider)
        resolved = ResolvedStage(stage="deep_review", provider=prov, model=(args.model or resolved.model))
    print(f"deep_review → {resolved.provider.name}/{resolved.model} @ {resolved.provider.base_url}", flush=True)
    print(f"paper: {title!r} ({len(qa_text)} chars)\n", flush=True)
    llm = build_client_for_stage(resolved)
    # Mirror production: the digest thinks (quality) unless the provider sets thinking_effort=off; trivial calls ride the feed model.
    from zotero_summarizer.services.llm.factory import build_client_for_provider
    llm_digest = build_client_for_provider(resolved.provider, resolved.model, enable_thinking=resolved.provider.thinking_on)

    lean_tier = bool(getattr(resolved.provider, "lean_deep_review", False))
    qr = config.quality_review
    tier_max_chars = int(qr.lean_max_text_chars if lean_tier else qr.max_text_chars)
    tier_runs = int(qr.lean_self_consistency_runs if lean_tier else qr.self_consistency_runs)
    # Same sub-call concurrency the background job uses, so this timing is a faithful
    # production receipt (remote → parallel rubric/goal calls; local → serial).
    sub_concurrency = deep_review_sub_concurrency(resolved.provider)
    print(
        f"tier: {'lean' if lean_tier else 'full'} | max_chars={tier_max_chars} | "
        f"rubric_runs={tier_runs} | sub_concurrency={sub_concurrency}\n",
        flush=True,
    )

    reporter = ReviewReporter(args.item_key, title, lambda _p: None)
    t0 = time.perf_counter()
    reporter.phase("digest", is_call=True)
    digest = quality_review.assess_digest(
        title=title, full_text=qa_text, config=config, llm=llm_digest, max_chars=tier_max_chars,
    )
    quality = quality_eval.evaluate_quality(
        title=title, full_text=qa_text, sections=[], digest=digest.model_dump(),
        llm=llm, max_chars=tier_max_chars,
        self_consistency_runs=tier_runs, reporter=reporter, sub_concurrency=sub_concurrency,
    )
    goals_fired = None
    if args.with_goals:
        goals = [g for g in (config.research_goals or []) if str(g).strip()]
        batch = lean_tier and bool(getattr(qr, "batch_goal_summaries", False))
        summaries = _paper_goal_summaries.summarize_for_goals(
            goals=goals, sections=[], full_text=qa_text, llm=llm, reporter=reporter,
            batch=batch, sub_concurrency=sub_concurrency,
        ) if goals else []
        goals_fired = sum(1 for g in summaries if getattr(g, "relevant", False))
    reporter.summary()

    out = {
        "item_key": args.item_key, "title": title,
        "elapsed_seconds": round(time.perf_counter() - t0, 1),
        "quality_band": quality.quality_band, "quality_grade": quality.grade,
        "digest": digest.model_dump(),
    }
    if goals_fired is not None:
        out["goals_fired"] = goals_fired
    print("\n" + json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def register_app(subparsers) -> None:
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

    prefetch = subparsers.add_parser(
        "prefetch-models",
        help="Download the HuggingFace models for offline use (run ONLINE once); "
             "--check reports cache status without downloading",
    )
    prefetch.add_argument("--project-root", default=None)
    prefetch.add_argument("--check", action="store_true", help="Report cache status, no download")
    prefetch.set_defaults(func=_prefetch_models)

    verify = subparsers.add_parser(
        "verify-deep-review",
        help="Headless end-to-end deep-review check on one already-built paper "
             "(uses its cached qa_text + the live deep_review model); prints per-phase timing + the digest",
    )
    verify.add_argument("--item-key", default="4NIMLFMV", help="paper item key with a built brief (data/paper_render/<key>)")
    verify.add_argument("--with-goals", action="store_true", help="also run the goal-summaries board (loads the embedder; heavier)")
    verify.add_argument("--provider", default=None,
                        help="Override the deep_review provider NAME (from goals.yaml routing) for this "
                             "run only — e.g. 'default' to drive the pipeline against a local ollama model "
                             "when the configured provider is unreachable.")
    verify.add_argument("--model", default=None, help="Override the deep_review model for this run only.")
    verify.add_argument("--project-root", default=None)
    verify.set_defaults(func=_verify_deep_review)
