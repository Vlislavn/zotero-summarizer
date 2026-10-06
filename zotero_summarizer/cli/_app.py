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
    """Read-only review of one built paper; sensitive capture requires CLI consent."""
    import logging
    import time

    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services._common import deep_review_sub_concurrency, read_config
    from zotero_summarizer.services.library import _map_reduce, _paper_goal_summaries, quality_eval
    from zotero_summarizer.services.library._deep_review_progress import ReviewReporter
    from zotero_summarizer.services.llm.factory import build_client_for_provider

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")

    from zotero_summarizer.cli._review_source import read_source

    state_path, state, qa_text, title, scope = read_source(settings, args.item_key)

    config = read_config(settings.config_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    if args.provider:
        from zotero_summarizer.models.providers import ResolvedStage
        prov = config.llm_routing.provider_by_name(args.provider)
        resolved = ResolvedStage(stage="deep_review", provider=prov, model=(args.model or resolved.model))
    print(f"deep_review → {resolved.provider.name}/{resolved.model}", flush=True)
    print(f"paper source: {len(qa_text)} chars; scope={scope}\n", flush=True)

    lean_tier = bool(getattr(resolved.provider, "lean_deep_review", False))
    qr = config.quality_review
    tier_max_chars = int(qr.lean_max_text_chars if lean_tier else qr.max_text_chars)
    tier_runs = int(qr.lean_self_consistency_runs if lean_tier else qr.self_consistency_runs)
    sub_concurrency = deep_review_sub_concurrency(resolved.provider)
    print(
        f"tier: {'lean' if lean_tier else 'full'} | max_chars={tier_max_chars} | "
        f"rubric_runs={tier_runs} | sub_concurrency={sub_concurrency}\n",
        flush=True,
    )

    from pathlib import Path
    from zotero_summarizer.services.library._review_attempt import Attempt, attempt_directory, file_identity, identity, record

    directory = attempt_directory(settings.data_dir)
    print(f"Local attempt diagnostics: {directory}", flush=True)
    with Attempt(capture=bool(getattr(args, 'capture_local', False)), directory=directory):
        record('config', identity(config.model_dump_json()), kind='identity')
        record('routing', {'provider': resolved.provider.name, 'model': resolved.model,
                           'strategy': qr.chunk_strategy, 'max_chars': tier_max_chars}, kind='identity')
        record('source_scope', {'scope': scope, 'artifact': file_identity(state_path)}, kind='identity')
        if state.get('pdf_path') and Path(state['pdf_path']).is_file():
            record('pdf', file_identity(Path(state['pdf_path'])), kind='identity')
        else:
            record('pdf', {'availability': 'not_present'}, kind='identity')
        from zotero_summarizer.api.errors import APIError
        from zotero_summarizer.services.library._source_admission import admit_source

        if not qa_text:
            raise APIError('extraction_empty', 'Paper state has no original source text', 422,
                           {'stage': 'source_admission', 'recovery': 'Rebuild the paper brief'})
        admit_source(qa_text)
        llm = build_client_for_provider(resolved.provider, resolved.model, enable_thinking=False)
        llm_digest = build_client_for_provider(resolved.provider, resolved.model, enable_thinking=False)
        reporter = ReviewReporter(args.item_key, title, lambda _p: None)
        t0 = time.perf_counter()
        reporter.phase("digest", is_call=True)
        digest = _map_reduce.digest_for_strategy(
            title, qa_text, config, map_llm=llm, reduce_llm=llm_digest,
            budget=_map_reduce.ChunkBudget(tier_max_chars, qr.map_chunk_chars, sub_concurrency),
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
    verify.add_argument("--capture-local", action="store_true",
                        help="Explicitly save sensitive prompts and decoded provider values locally; never upload.")
    verify.add_argument("--with-goals", action="store_true", help="also run the goal-summaries board (loads the embedder; heavier)")
    verify.add_argument("--provider", default=None,
                        help="Override the deep_review provider NAME (from goals.yaml routing) for this "
                             "run only — e.g. 'default' to drive the pipeline against a local ollama model "
                             "when the configured provider is unreachable.")
    verify.add_argument("--model", default=None, help="Override the deep_review model for this run only.")
    verify.add_argument("--project-root", default=None)
    verify.set_defaults(func=_verify_deep_review)
