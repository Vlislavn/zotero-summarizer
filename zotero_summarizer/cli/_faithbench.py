"""``zotero-summarizer faithbench`` — faithfulness mini-benchmark CLI.

Four verbs (build → run → judge → report); see
``services/faithbench/README.md`` for the pipeline. The model under test is
whatever ``llm_routing.deep_review`` resolves to; the builder/judge default to
the remote ``CUSTOM_BASE_URL`` endpoint with the pinned
``DEFAULT_JUDGE_MODEL``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from zotero_summarizer.settings import Settings
from zotero_summarizer.cli._helpers import _utc_iso_now


def _print_progress(message: str) -> None:
    print(f"  {message}", flush=True)


def _remote_llm(base_url_env: str, api_key_env: str, model: str):
    """OpenAI-compatible client for the remote builder/judge endpoint.

    Both env vars must be set (.env) — a missing URL or key is a hard error,
    the benchmark must never silently fall back to another endpoint.
    """
    from zotero_summarizer.services._adapters import build_llm
    from zotero_summarizer.services.faithbench._constants import JUDGE_MAX_TOKENS

    base_url = os.getenv(base_url_env, "").strip()
    if not base_url:
        raise RuntimeError(f"environment variable {base_url_env} is not set (judge/builder endpoint URL)")
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"environment variable {api_key_env} is not set (judge/builder API key)")
    return build_llm(base_url, model, api_key, max_tokens=JUDGE_MAX_TOKENS)


def _run_paths(settings: Settings, run_id: str):
    from zotero_summarizer.services.faithbench import RunPaths

    return RunPaths(run_dir=settings.faithbench_dir / "runs" / run_id)


def _load_manifest(paths) -> dict:
    if not paths.manifest.exists():
        raise FileNotFoundError(
            f"no manifest at {paths.manifest}; is --run-id correct? (run `faithbench run` first)"
        )
    return json.loads(paths.manifest.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _faithbench_build(args: argparse.Namespace) -> int:
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services import run_log
    from zotero_summarizer.services._adapters import build_pdf_extractor
    from zotero_summarizer.services.faithbench import build_items, select_papers
    from zotero_summarizer.services.faithbench import _dataset
    from zotero_summarizer.services.faithbench._constants import DEFAULT_JUDGE_MODEL

    settings = Settings.load(project_root=args.project_root)
    faithbench_dir = settings.faithbench_dir
    builder_model = args.builder_model or DEFAULT_JUDGE_MODEL
    builder_llm = _remote_llm(args.builder_base_url_env, args.builder_api_key_env, builder_model)

    papers = select_papers(
        reader=ZoteroReader(settings.zotero_data_dir),
        extractor=build_pdf_extractor(),
        papers_dir=faithbench_dir / "papers",
        pdf_root=settings.pdf_root,
        n_papers=args.n_papers,
        item_keys=[k.strip() for k in args.papers.split(",")] if args.papers else None,
        collection=args.collection,
        tag=args.tag,
        progress_cb=_print_progress,
    )
    items = build_items(
        papers=papers,
        builder_llm=builder_llm,
        qa_per_paper=args.qa_per_paper,
        traps_per_paper=args.traps_per_paper,
        progress_cb=_print_progress,
    )

    version = _dataset.next_benchmark_version(faithbench_dir)
    meta = _dataset.BenchmarkMeta(
        version=version,
        created_at=_utc_iso_now(),
        builder_model=builder_model,
        git_commit=run_log.short_git_commit(settings.project_root),
        papers=[
            _dataset.PaperManifestEntry(
                item_key=p.item_key, title=p.title,
                text_sha256=p.text_sha256, n_chars=len(p.text),
            )
            for p in papers
        ],
        config={
            "n_papers": args.n_papers, "qa_per_paper": args.qa_per_paper,
            "traps_per_paper": args.traps_per_paper,
        },
    )
    bench_path = _dataset.benchmark_path(faithbench_dir, version)
    n_items = _dataset.save_benchmark(bench_path, meta, items)
    csv_path = faithbench_dir / f"benchmark_v{version}.review.csv"
    _dataset.export_review_csv(csv_path, items, {p.item_key: p.text for p in papers})

    print(json.dumps({
        "benchmark": str(bench_path),
        "review_csv": str(csv_path),
        "version": version,
        "papers": len(papers),
        "items": n_items,
        "qa": sum(1 for i in items if i.kind == "qa"),
        "traps": sum(1 for i in items if i.kind == "trap"),
        "builder_model": builder_model,
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _resolve_benchmark(settings: Settings, spec: str) -> Path:
    from zotero_summarizer.services.faithbench import _dataset

    if spec == "latest":
        return _dataset.latest_benchmark_path(settings.faithbench_dir)
    path = Path(spec).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"benchmark file not found: {path}")
    return path


def _faithbench_run(args: argparse.Namespace) -> int:
    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services import run_log
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.faithbench import run_benchmark
    from zotero_summarizer.services.faithbench import _dataset
    from zotero_summarizer.services.faithbench._runner import write_or_check_manifest
    from zotero_summarizer.services.llm.factory import build_client_for_stage

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    # Per-run model sweep: override the resolved stage (and thus the manifest's
    # model_under_test + resume guard) without mutating goals.yaml. Fail loud on
    # an unknown provider — the config is the input, never silently coerced.
    if args.provider or args.model:
        from zotero_summarizer.models.providers import ResolvedStage

        prov_name = args.provider or resolved.provider.name
        try:
            provider = config.llm_routing.provider_by_name(prov_name)
        except KeyError:
            raise SystemExit(
                f"--provider {prov_name!r} not in llm_routing.providers "
                f"{[p.name for p in config.llm_routing.providers]}"
            )
        resolved = ResolvedStage(stage="deep_review", provider=provider, model=args.model or resolved.model)
    llm = build_client_for_stage(resolved)

    conditions = tuple(c.strip() for c in args.conditions.split(",") if c.strip())
    tracks = tuple(t.strip() for t in args.tracks.split(",") if t.strip())
    decompose_llm = None
    if "claims" in tracks:
        from zotero_summarizer.services.faithbench._constants import DEFAULT_JUDGE_MODEL

        decompose_llm = _remote_llm(
            args.judge_base_url_env, args.judge_api_key_env,
            args.judge_model or DEFAULT_JUDGE_MODEL,
        )

    bench_path = _resolve_benchmark(settings, args.benchmark)
    meta, items = _dataset.load_benchmark(bench_path)
    run_id = args.run_id or run_log.make_run_id("faithbench")
    paths = _run_paths(settings, run_id)

    manifest = write_or_check_manifest(paths, {
        "run_id": run_id,
        "started_at": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "model": resolved.model,
        "provider_name": resolved.provider.name,
        "base_url": resolved.provider.base_url,
        "benchmark_path": str(bench_path),
        "benchmark_sha256": run_log.file_sha256(bench_path, prefix_len=64),
        "conditions": list(conditions),
        "tracks": list(tracks),
        "runs": args.runs,
        # Snapshot: the digest prompt is conditioned on these goals, and the
        # judge applies the goal-aware read_why standard against the SAME text
        # even if goals.yaml changes between run and judge. Not a guard field.
        "research_goals": [g for g in (config.research_goals or []) if str(g).strip()],
    })

    print(
        f"run {run_id}: model={resolved.model!r} via {resolved.provider.base_url} "
        f"(local={resolved.provider.is_local} → serial={resolved.provider.is_local})",
        flush=True,
    )
    counts = run_benchmark(
        run_id=run_id, meta=meta, items=items,
        papers_dir=settings.faithbench_dir / "papers",
        paths=paths, llm=llm, config=config, decompose_llm=decompose_llm,
        conditions=conditions, tracks=tracks, runs=args.runs,
        limit=args.limit, retry_errors=args.retry_errors,
        serial=resolved.provider.is_local,
        max_workers=settings.triage_job_concurrency,
        progress_cb=_print_progress,
    )
    print(json.dumps({"run_id": run_id, "manifest": str(paths.manifest), **counts,
                      "next": f"zotero-summarizer faithbench judge --run-id {run_id}"}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# judge / report
# ---------------------------------------------------------------------------


def _faithbench_judge(args: argparse.Namespace) -> int:
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.faithbench import judge_run
    from zotero_summarizer.services.faithbench import _dataset

    settings = Settings.load(project_root=args.project_root)
    paths = _run_paths(settings, args.run_id)
    manifest = _load_manifest(paths)
    meta, items = _dataset.load_benchmark(Path(manifest["benchmark_path"]))
    judge_llm = _remote_llm(args.judge_base_url_env, args.judge_api_key_env, args.judge_model)
    config = read_config(settings.config_path)

    # Goals for the read_why standard: prefer the run-time snapshot; runs
    # predating the snapshot fall back to the current config's goals.
    goals = manifest.get("research_goals")
    if goals is None:
        goals = list(config.research_goals or [])
    counts = judge_run(
        meta=meta, items=items,
        papers_dir=settings.faithbench_dir / "papers",
        paths=paths, judge_llm=judge_llm, judge_model=args.judge_model,
        max_text_chars=int(config.quality_review.max_text_chars),
        research_goals="; ".join(g for g in goals if str(g).strip()),
        force=args.force, progress_cb=_print_progress,
    )
    print(json.dumps({"run_id": args.run_id, "judge_model": args.judge_model, **counts,
                      "next": f"zotero-summarizer faithbench report --run-id {args.run_id}"}, indent=2))
    return 0


def _faithbench_report(args: argparse.Namespace) -> int:
    from zotero_summarizer.services.faithbench import build_report
    from zotero_summarizer.services.faithbench import _dataset

    settings = Settings.load(project_root=args.project_root)
    paths = _run_paths(settings, args.run_id)
    manifest = _load_manifest(paths)
    _, items = _dataset.load_benchmark(Path(manifest["benchmark_path"]))
    report = build_report(
        paths=paths, items=items, manifest=manifest,
        benchmark_path=Path(manifest["benchmark_path"]),
        faithbench_dir=settings.faithbench_dir,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nreport.md: {paths.report_md}")
    return 0


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def _add_judge_endpoint_args(parser: argparse.ArgumentParser, *, prefix: str) -> None:
    from zotero_summarizer.services.faithbench._constants import (
        DEFAULT_JUDGE_API_KEY_ENV,
        DEFAULT_JUDGE_BASE_URL_ENV,
    )

    parser.add_argument(
        f"--{prefix}-base-url-env", default=DEFAULT_JUDGE_BASE_URL_ENV,
        dest=f"{prefix}_base_url_env",
        help=f"Env var holding the endpoint URL. Default: {DEFAULT_JUDGE_BASE_URL_ENV}.",
    )
    parser.add_argument(
        f"--{prefix}-api-key-env", default=DEFAULT_JUDGE_API_KEY_ENV,
        dest=f"{prefix}_api_key_env",
        help=f"Env var holding the API key. Default: {DEFAULT_JUDGE_API_KEY_ENV}.",
    )


def register_faithbench(subparsers) -> None:
    from zotero_summarizer.services.faithbench._constants import (
        DEFAULT_JUDGE_MODEL,
        DEFAULT_N_PAPERS,
        DEFAULT_QA_PER_PAPER,
        DEFAULT_TRAPS_PER_PAPER,
    )

    fb = subparsers.add_parser(
        "faithbench",
        help="Faithfulness mini-benchmark for the deep-review / paper-Q&A pipeline "
             "(build -> run -> judge -> report). See services/faithbench/README.md.",
    )
    fb_sub = fb.add_subparsers(dest="faithbench_command", required=True)

    build = fb_sub.add_parser(
        "build",
        help="Freeze paper texts, auto-generate span-verified QA + traps, write "
             "benchmark_v<N>.jsonl + a reviewable CSV.",
    )
    build.add_argument("--n-papers", type=int, default=DEFAULT_N_PAPERS)
    build.add_argument("--qa-per-paper", type=int, default=DEFAULT_QA_PER_PAPER)
    build.add_argument("--traps-per-paper", type=int, default=DEFAULT_TRAPS_PER_PAPER)
    build.add_argument("--papers", default=None, help="Comma-separated Zotero item keys (overrides selection).")
    build.add_argument("--collection", default=None, help="Restrict selection to a collection key.")
    build.add_argument("--tag", default=None, help="Restrict selection to a tag.")
    build.add_argument(
        "--builder-model", default=None,
        help=f"QA-builder model on the remote endpoint. Default: {DEFAULT_JUDGE_MODEL} (the pinned judge).",
    )
    _add_judge_endpoint_args(build, prefix="builder")
    build.add_argument("--project-root", default=None)
    build.set_defaults(func=_faithbench_build)

    run = fb_sub.add_parser(
        "run",
        help="Ask the deep_review-stage model every benchmark question (and build "
             "digests for the claims track). Resumable: re-invoke with the same "
             "--run-id after a crash/Ctrl-C. Defaults ~= 112 local calls ~= 3-3.5h "
             "on a local 35B; --runs 3 is an overnight job.",
    )
    run.add_argument("--benchmark", default="latest", help="Path to benchmark_v<N>.jsonl, or 'latest'.")
    run.add_argument("--run-id", default=None, help="Resume an existing run (same flags required).")
    run.add_argument("--provider", default=None,
                     help="Override the deep_review provider NAME for THIS run only (must exist in "
                          "goals.yaml llm_routing.providers). Lets you sweep models without editing config.")
    run.add_argument("--model", default=None,
                     help="Override the deep_review model for THIS run only. Recorded verbatim in manifest.json.")
    run.add_argument("--conditions", default="full_text,retrieval")
    run.add_argument("--tracks", default="qa,claims")
    run.add_argument("--runs", type=int, default=1, help="Repetitions per item (3 for variance bars).")
    run.add_argument("--limit", type=int, default=None, help="Cap QA/trap items (smoke runs).")
    run.add_argument("--retry-errors", action="store_true",
                     help="Re-attempt trials whose last attempt was an exception.")
    run.add_argument(
        "--judge-model", default=None,
        help=f"Claim-decomposition model. Default: {DEFAULT_JUDGE_MODEL}.",
    )
    _add_judge_endpoint_args(run, prefix="judge")
    run.add_argument("--project-root", default=None)
    run.set_defaults(func=_faithbench_run)

    judge = fb_sub.add_parser(
        "judge",
        help="Judge a run: deterministic hard ladder first, pinned LLM judge for the "
             "residual band only. Re-runnable; --force re-judges everything (responses untouched).",
    )
    judge.add_argument("--run-id", required=True)
    judge.add_argument(
        "--judge-model", default=DEFAULT_JUDGE_MODEL,
        help=f"Pinned judge model id on the remote endpoint. Default: {DEFAULT_JUDGE_MODEL}.",
    )
    _add_judge_endpoint_args(judge, prefix="judge")
    judge.add_argument("--force", action="store_true", help="Discard prior judgments and re-judge.")
    judge.add_argument("--project-root", default=None)
    judge.set_defaults(func=_faithbench_judge)

    report = fb_sub.add_parser(
        "report",
        help="Aggregate judgments into report.json + report.md and append a headline "
             "to data/faithbench/faithbench-runs.jsonl.",
    )
    report.add_argument("--run-id", required=True)
    report.add_argument("--project-root", default=None)
    report.set_defaults(func=_faithbench_report)
