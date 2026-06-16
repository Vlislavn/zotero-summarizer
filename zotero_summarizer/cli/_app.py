from __future__ import annotations

import argparse
import json
import os

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


def _model_targets(config) -> list[tuple[str, str]]:
    """The HuggingFace repos the app needs cached for full offline use:
    the SPECTER2 gate encoder + adapter, the corpus embedding model, and the
    search reranker (the last two come from goals.yaml `corpus.*`)."""
    from zotero_summarizer.services.model.classifier_const import (
        SPECTER2_ADAPTER_NAME,
        SPECTER2_MODEL_NAME,
    )
    targets = [
        ("gate encoder (SPECTER2 base)", SPECTER2_MODEL_NAME),
        ("gate adapter (SPECTER2 proximity)", SPECTER2_ADAPTER_NAME),
        ("corpus embeddings", config.corpus.embedding_model),
        ("reranker (cross-encoder)", config.corpus.reranker_model),
    ]
    if getattr(config.quality_review, "shadow_claim_check", False):
        from zotero_summarizer.services.model.claim_checker import hf_repo_for
        targets.append(
            ("claim-check (MiniCheck encoder)", hf_repo_for(config.quality_review.claim_check_model))
        )
    return targets


def _cache_report(targets: list[tuple[str, str]]) -> list[dict]:
    """For each target repo: ``{label, repo_id, cached, size_mb}`` from the local
    HuggingFace cache scan (no network)."""
    from huggingface_hub import scan_cache_dir

    sizes = {repo.repo_id: int(repo.size_on_disk) for repo in scan_cache_dir().repos}
    return [
        {
            "label": label,
            "repo_id": repo_id,
            "cached": repo_id in sizes,
            "size_mb": round(sizes.get(repo_id, 0) / 1e6, 1),
        }
        for label, repo_id in targets
    ]


def _prefetch_models(args: argparse.Namespace) -> int:
    """Pre-download (or --check) the HF models needed for offline use."""
    settings = Settings.load(project_root=args.project_root)
    from zotero_summarizer.services._common import read_config

    config = read_config(settings.config_path)
    targets = _model_targets(config)

    if args.check:
        report = _cache_report(targets)
        print(json.dumps(
            {"offline_ready": all(r["cached"] for r in report), "models": report},
            indent=2,
        ))
        return 0

    # This command's job is to GO ONLINE and warm the cache, so clear any offline
    # flag the CLI applied from ZS_OFFLINE/.env for THIS process only.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)

    print(f"Prefetching {len(targets)} models (downloads on first run)…", flush=True)
    from zotero_summarizer.storage.corpus import EmbeddingCache
    EmbeddingCache(settings.corpus_db_path, config.corpus.embedding_model)._load_model()
    from zotero_summarizer.services.model.reranker import get_reranker
    get_reranker(config.corpus.reranker_model)._load()
    from zotero_summarizer.services.model.classifier_embed import _load_specter2
    _load_specter2()
    if getattr(config.quality_review, "shadow_claim_check", False):
        from zotero_summarizer.services.model.claim_checker import get_claim_checker
        get_claim_checker(config.quality_review.claim_check_model)._load()

    report = _cache_report(targets)
    print(json.dumps(
        {"offline_ready": all(r["cached"] for r in report), "models": report},
        indent=2,
    ))
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
    # Mirror production: the digest thinks (quality), the trivial calls don't (speed).
    from zotero_summarizer.services.llm.factory import build_client_for_provider
    llm_digest = build_client_for_provider(resolved.provider, resolved.model, enable_thinking=True)

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

