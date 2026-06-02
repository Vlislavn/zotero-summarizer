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
    return [
        ("gate encoder (SPECTER2 base)", SPECTER2_MODEL_NAME),
        ("gate adapter (SPECTER2 proximity)", SPECTER2_ADAPTER_NAME),
        ("corpus embeddings", config.corpus.embedding_model),
        ("reranker (cross-encoder)", config.corpus.reranker_model),
    ]


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

    report = _cache_report(targets)
    print(json.dumps(
        {"offline_ready": all(r["cached"] for r in report), "models": report},
        indent=2,
    ))
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

