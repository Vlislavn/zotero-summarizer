from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Generator, Sequence

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.migrations import migrate_existing


def _resolve_feed_ids(raw: str, settings: Settings) -> list[int]:
    """Resolve a comma-separated string of feed tokens to library IDs.

    Each token may be:
    - A numeric string (used directly as ``library_id``)
    - A name substring (case-insensitive match against Zotero feed names)

    Raises ``SystemExit`` with a descriptive message on ambiguity or no match.
    """
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services.feeds import list_feed_groups

    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        return []

    needs_name_lookup = any(not t.lstrip("-").isdigit() for t in tokens)
    feed_groups: list[dict] = []
    if needs_name_lookup:
        try:
            feed_groups = list_feed_groups(ZoteroReader(settings.zotero_data_dir))
        except Exception as exc:
            print(f"ERROR: could not read feed list from Zotero: {exc}", file=sys.stderr)
            raise SystemExit(2)

    ids: list[int] = []
    for token in tokens:
        if token.lstrip("-").isdigit():
            ids.append(int(token))
        else:
            matches = [f for f in feed_groups if token.lower() in f["name"].lower()]
            if not matches:
                available = ", ".join(
                    f'"{f["name"]}" (ID {f["library_id"]})' for f in feed_groups[:8]
                )
                print(
                    f"ERROR: no feed matches {token!r}.\n"
                    f"Run `feeds list` to see all feeds. Some options: {available}",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            if len(matches) > 1:
                options = ", ".join(f'"{m["name"]}" (ID {m["library_id"]})' for m in matches)
                print(
                    f"ERROR: ambiguous feed name {token!r} — {len(matches)} matches: {options}.\n"
                    "Be more specific or use the numeric ID from `feeds list`.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            ids.append(int(matches[0]["library_id"]))
    return ids


@contextlib.contextmanager
def _feeds_lock(project_root: Path) -> Generator[None, None, None]:
    """Exclusive lock that prevents simultaneous `feeds run` / `feeds serve` calls.

    Uses a PID file at ``{project_root}/feeds.lock``.  If the PID in the file
    belongs to an active process the command exits with an error message.
    Stale locks (dead PID) are silently overwritten.

    ``feeds tick`` and ``feeds select-daily`` do NOT use this lock — they are
    explicitly designed to be run alongside a daemon.
    """
    lock_path = project_root / "feeds.lock"
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            os.kill(existing_pid, 0)  # Signal 0: raises if process does not exist
            print(
                f"ERROR: a feeds process is already running (PID {existing_pid}).\n"
                "Stop it first (Ctrl-C or kill), then retry.\n"
                "Tip: `feeds tick` can run alongside the daemon for a one-shot batch.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        except (ProcessLookupError, PermissionError):
            pass  # Stale lock — overwrite it
        except ValueError:
            pass  # Corrupt lock file — overwrite it

    lock_path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


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
        from zotero_summarizer.services.feeds import run_daemon_tick

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
        from zotero_summarizer.services.feeds import run_daemon_loop

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
        from zotero_summarizer.services.feeds import run_daemon_tick

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


def _goldenset_export(args: argparse.Namespace) -> int:
    """Export the user's existing Zotero engagement signals as a golden dataset."""
    from zotero_summarizer.services import goldenset

    settings = Settings.load(project_root=args.project_root)
    output_dir = Path(args.output_dir or settings.project_root)
    csv_path = output_dir / "zotero-summarizer-golden.csv"
    jsonl_path = output_dir / "zotero-summarizer-golden.jsonl"

    result = goldenset.export_golden_dataset(
        zotero_data_dir=settings.zotero_data_dir,
        output_csv=csv_path,
        output_jsonl=jsonl_path,
        abstract_chars=args.abstract_chars,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _goldenset_train_classifier(args: argparse.Namespace) -> int:
    """Train a classifier on the golden CSV and persist it for the daemon gate.

    Phase 1.13: writes ``~/.cache/zotero-summarizer/models/{name}.{joblib,json}``
    plus a ``classifier-runs.jsonl`` entry. The daemon reads these at startup
    when ``classifier_gate.enabled: true`` in goals.yaml.
    """
    from zotero_summarizer.services import classifier_persistence, run_log
    from zotero_summarizer.services._common import read_config

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)

    golden_csv = Path(args.input or settings.project_root / "zotero-summarizer-golden.csv")
    if not golden_csv.exists():
        raise FileNotFoundError(
            f"Golden CSV not found at {golden_csv}; run `goldenset export` first."
        )

    output_dir = Path(args.output_dir) if args.output_dir else classifier_persistence.DEFAULT_MODEL_DIR

    def _progress(done: int, total: int) -> None:
        print(f"  featurising {done}/{total}", flush=True)

    if args.force:
        trained = classifier_persistence.train_and_save(
            golden_csv,
            classifier_name=args.classifier,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            output_dir=output_dir,
            n_folds=args.folds,
            pca_dim=args.pca_dim,
            progress_cb=_progress,
        )
    else:
        trained = classifier_persistence.load_or_train(
            golden_csv,
            classifier_name=args.classifier,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            output_dir=output_dir,
            force_retrain=False,
            n_folds=args.folds,
            pca_dim=args.pca_dim,
        )

    # Append run-log entry so the FAIR audit trail is complete.
    log_path = settings.project_root / "classifier-runs.jsonl"
    entry = {
        "run_id": run_log.make_run_id(f"train_{trained.classifier_name}"),
        "timestamp": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "classifier": trained.classifier_name,
        "type": "train_artifact",
        "model_path": str(output_dir / f"{trained.classifier_name}.joblib"),
        "golden_csv": str(golden_csv),
        "golden_csv_sha256_prefix": trained.golden_csv_sha256[:12],
        "thresholds": {
            "keep": round(trained.t_keep, 4),
            "must": round(trained.t_must, 4),
            "could": round(trained.t_could, 4),
        },
        "training_metadata": trained.training_metadata,
        "config": {
            "n_folds": args.folds,
            "pca_dim": args.pca_dim,
        },
    }
    run_log.append_run(log_path, entry)
    entry["run_log_path"] = str(log_path)

    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def _goldenset_eval_baseline(args: argparse.Namespace) -> int:
    """Phase 1.16 Step 0 — baseline + learning-curve measurement."""
    from zotero_summarizer.services import eval_baseline
    from zotero_summarizer.services._common import read_config

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)

    golden_csv = Path(args.input or settings.project_root / "zotero-summarizer-golden.csv")
    rows = eval_baseline.load_golden_rows(golden_csv)

    timestamp = _utc_iso_now().replace(":", "").replace("-", "")[:15]

    def _progress(done: int, total: int) -> None:
        print(f"  featurising {done}/{total}", flush=True)

    if args.learning_curve:
        if args.learning_curve_fractions:
            fractions = tuple(
                float(s.strip()) for s in args.learning_curve_fractions.split(",")
            )
        else:
            fractions = eval_baseline.DEFAULT_LEARNING_CURVE_FRACTIONS
        report = eval_baseline.run_learning_curve(
            rows,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            classifier_name=args.classifier,
            fractions=fractions,
            n_folds=args.n_folds,
            n_bootstrap=args.n_bootstrap,
            pca_dim=args.pca_dim,
            seed=args.seed,
            progress_cb=_progress,
        )
        out_path = Path(args.output or settings.project_root / f"learning-curve-{timestamp}.json")
        payload = eval_baseline.learning_curve_to_dict(report)
    else:
        report = eval_baseline.run_baseline(
            rows,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            classifier_name=args.classifier,
            n_repeats=args.n_repeats,
            n_folds=args.n_folds,
            n_bootstrap=args.n_bootstrap,
            pca_dim=args.pca_dim,
            seed=args.seed,
            progress_cb=_progress,
        )
        out_path = Path(args.output or settings.project_root / f"eval-baseline-{timestamp}.json")
        payload = eval_baseline.report_to_dict(report)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["output_path"] = str(out_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _goldenset_compare(args: argparse.Namespace) -> int:
    """Print every classifier's latest run side-by-side from the run log."""
    from zotero_summarizer.services import run_log

    settings = Settings.load(project_root=args.project_root)
    log_path = Path(args.log or settings.project_root / "classifier-runs.jsonl")
    runs = run_log.load_runs(log_path)
    if not runs:
        print(json.dumps({"error": f"no runs found in {log_path}"}, indent=2))
        return 1

    latest = run_log.latest_per_classifier(runs)
    split = args.split

    header = (
        f"{'classifier':<22} {'auc':>6} {'binary_F1':>10} {'binary_P':>9} "
        f"{'binary_R':>9} {'must_F1':>8} {'must_P':>7} {'must_R':>7} "
        f"{'n':>4} {'run_id':<30}"
    )
    print(header)
    print("-" * len(header))

    def _f(v: Any, w: int = 6, places: int = 3) -> str:
        if v is None or v == "":
            return "  —".rjust(w)
        try:
            return f"{float(v):.{places}f}".rjust(w)
        except (TypeError, ValueError):
            return str(v).rjust(w)

    rows_out = []
    for name, run in latest.items():
        block = run.get(split) or {}
        m = block.get("metrics_vs_gold") or {}
        b = m.get("binary") or {}
        mr = (m.get("per_class") or {}).get("must_read", {}) or {}
        rows_out.append(
            f"{name[:22]:<22} {_f(block.get('auc'), 6, 3)} "
            f"{_f(b.get('f1'), 10, 3)} {_f(b.get('precision'), 9, 3)} "
            f"{_f(b.get('recall'), 9, 3)} "
            f"{_f(mr.get('f1'), 8, 3)} {_f(mr.get('precision'), 7, 3)} "
            f"{_f(mr.get('recall'), 7, 3)} "
            f"{m.get('total', 0):>4} {run.get('run_id', '')[:30]:<30}"
        )
    # Sort by binary F1 descending so the best run is at the top.
    def _f1(line: str) -> float:
        try:
            return -float(line.split()[2])
        except (IndexError, ValueError):
            return 0.0
    rows_out.sort(key=_f1)
    for line in rows_out:
        print(line)
    return 0


def _goldenset_classify_llm(args: argparse.Namespace) -> int:
    """4th classifier in the comparison: LLM reads title+abstract and classifies directly."""
    import csv as _csv
    import os
    import time

    from zotero_summarizer.services import classifier, llm_classifier
    from zotero_summarizer.services._adapters import build_llm
    from zotero_summarizer.services._common import read_config, setup_logging

    settings = Settings.load(project_root=args.project_root)
    setup_logging()
    config = read_config(settings.config_path)

    api_base = (args.api_base or config.llm.api_base).rstrip()
    api_key_env = (args.api_key_env or config.llm.api_key_env).strip()
    model_name = (args.model or config.llm.refine_model).strip()
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"environment variable {api_key_env} is not set; "
            "add it to .env or export it before running."
        )
    # Drop OnPrem-specific extra_body when the caller pointed at a different
    # backend — OpenAI / OpenRouter / custom endpoints reject unknown args.
    extra_body = config.llm.extra_body if not args.api_base else None
    llm = build_llm(api_base, model_name, api_key, max_tokens=2048, extra_body=extra_body)

    input_csv = Path(args.input or settings.project_root / "zotero-summarizer-golden.csv")
    if not input_csv.exists():
        raise FileNotFoundError(f"golden CSV not found at {input_csv}")
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(_csv.DictReader(f))

    rows = all_rows
    if args.strength:
        wanted = {s.strip() for s in args.strength.split(",") if s.strip()}
        rows = [r for r in rows if (r.get("gold_signal_strength") or "").strip() in wanted]
    if args.limit is not None and args.limit > 0:
        rows = rows[: args.limit]

    print(
        f"classifying {len(rows)} rows via {model_name!r} at {api_base} "
        f"(workers={args.workers}) …",
        flush=True,
    )
    start = time.perf_counter()

    def _progress(done: int, total: int) -> None:
        print(f"  classified {done}/{total}", flush=True)

    classifications = llm_classifier.classify_papers_with_llm(
        rows,
        llm,
        research_goals=list(config.research_goals or []),
        workers=args.workers,
        progress_cb=_progress,
    )
    classifier_slug = (args.classifier_name or _slugify_model(model_name)).strip()
    updated = llm_classifier.write_predictions_to_csv(
        input_csv, classifications, classifier_name=classifier_slug,
    )
    elapsed = time.perf_counter() - start

    strength_filter = (
        {s.strip() for s in args.strength.split(",") if s.strip()}
        if args.strength
        else None
    )
    priority_col = f"cls_{classifier_slug}_priority"
    metrics = classifier.compute_metrics_against_gold(
        input_csv,
        strength_filter=strength_filter,
        priority_column=priority_col,
    )

    from zotero_summarizer.services import run_log
    summary = {
        "run_id": run_log.make_run_id(classifier_slug),
        "timestamp": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "classifier": classifier_slug,
        "type": "llm_judge",
        "model": model_name,
        "api_base": api_base,
        "config": {
            "workers": args.workers,
            "strength_filter": sorted(strength_filter) if strength_filter else None,
            "limit": args.limit,
        },
        "rows_processed": len(rows),
        "rows_with_priority": sum(1 for c in classifications if c.priority),
        "rows_failed": sum(1 for c in classifications if c.error),
        "csv_updated_rows": updated,
        "elapsed_seconds": round(elapsed, 1),
        "cv": {
            "n_rows": metrics.get("total", 0),
            "n_positive": metrics.get("binary", {}).get("support", 0),
            "auc": None,
            "metrics_vs_gold": metrics,
        },
        "holdout": {},
        "input_csv": str(input_csv),
        "input_csv_sha256_prefix": run_log.file_sha256(input_csv),
    }
    _persist_run_log(settings, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _slugify_model(model_name: str) -> str:
    """Make a filesystem-safe / column-safe classifier slug from a model name.

    ``nvidia/nemotron-3-super-120b-a12b:free`` → ``llm_nvidia_nemotron_3_super``.
    """
    import re

    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", model_name).strip("_").lower()
    parts = cleaned.split("_")[:5]
    return "llm_" + "_".join(parts)


def _goldenset_analyze_notes(args: argparse.Namespace) -> int:
    """Classify user-written Zotero notes via the LLM and write a review CSV.

    Uses goals.yaml's ``llm`` block by default; ``--api-base``, ``--api-key-env``
    and ``--model`` let the caller route this single command through a
    different OpenAI-compatible endpoint (e.g. OpenRouter free models) without
    touching ``goals.yaml``.
    """
    import os

    from zotero_summarizer.services import note_analyzer
    from zotero_summarizer.services._adapters import build_llm
    from zotero_summarizer.services._common import read_config, setup_logging

    settings = Settings.load(project_root=args.project_root)
    setup_logging()
    config = read_config(settings.config_path)

    api_base = (args.api_base or config.llm.api_base).rstrip()
    api_key_env = (args.api_key_env or config.llm.api_key_env).strip()
    model_name = (args.model or config.llm.refine_model).strip()
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"environment variable {api_key_env} is not set; "
            "add it to .env or export it before running."
        )
    # OpenRouter (and other non-Ollama backends) reject the OnPrem-specific
    # `chat_template_kwargs` extra_body, so only forward it when the caller
    # didn't override the api_base.
    extra_body = config.llm.extra_body if not args.api_base else None
    llm = build_llm(api_base, model_name, api_key, max_tokens=4096, extra_body=extra_body)

    notes = note_analyzer.pull_candidate_notes(
        settings.zotero_data_dir,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        limit=args.limit,
    )
    if not notes:
        print(json.dumps({"error": "no candidate notes found"}, indent=2))
        return 1

    print(
        f"classifying {len(notes)} candidate notes via {model_name!r} at "
        f"{api_base} …",
        flush=True,
    )

    def _progress(done: int, total: int) -> None:
        print(f"  classified {done}/{total}", flush=True)

    analyses = note_analyzer.classify_notes(notes, llm, progress_cb=_progress)
    output_csv = Path(args.output or settings.project_root / "note-analyses.csv")
    note_analyzer.write_analyses_csv(analyses, output_csv)

    summary = {
        "model": model_name,
        "api_base": api_base,
        "candidates": len(notes),
        "classified": sum(1 for a in analyses if a.llm_priority),
        "skipped": sum(1 for a in analyses if not a.llm_priority),
        "by_priority": note_analyzer.distribution(analyses),
        "output_csv": str(output_csv),
    }
    print()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _goldenset_predict_feed(args: argparse.Namespace) -> int:
    """Pull unread items from a feed, predict 4-class priority, save for review."""
    import csv as _csv

    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services import classifier

    settings = Settings.load(project_root=args.project_root)
    feed_filter: list[int] | None = None
    if args.feeds:
        feed_filter = _resolve_feed_ids(args.feeds, settings)

    # Load training rows from the golden CSV.
    input_csv = Path(args.golden_csv or settings.project_root / "zotero-summarizer-golden.csv")
    if not input_csv.exists():
        raise FileNotFoundError(f"Golden CSV not found at {input_csv}; run `goldenset export` first.")
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        training_rows = list(_csv.DictReader(f))

    # Pull unread items from the requested feed.
    reader = ZoteroReader(settings.zotero_data_dir)
    feed_library_ids = feed_filter or [int(f["library_id"]) for f in reader.get_feed_groups()]

    # Build a skip-set of already-annotated feed items so we surface fresh ones
    # only when --exclude-annotated is set. Annotated rows live in the golden
    # CSV with item_key formatted as "feed:<integer_id>".
    skip_ids: set[str] = set()
    if args.exclude_annotated:
        for r in training_rows:
            key = (r.get("item_key") or "").strip()
            if key.startswith("feed:"):
                skip_ids.add(key.removeprefix("feed:"))

    new_items: list[dict] = []
    # Pull more than requested so we have headroom after the skip filter.
    fetch_target = args.limit * 4 if args.exclude_annotated else args.limit
    for lib_id in feed_library_ids:
        items = reader.get_feed_items(
            feed_library_id=lib_id,
            unread_only=True,
            order="newest_first",
            limit=fetch_target,
        )
        for it in items:
            if str(it.get("item_id", "")) in skip_ids:
                continue
            new_items.append(it)
            if len(new_items) >= args.limit:
                break
        if len(new_items) >= args.limit:
            break
    new_items = new_items[: args.limit]

    if not new_items:
        print(json.dumps({"error": "no unread items found", "feeds": feed_library_ids}, indent=2))
        return 1

    def _progress(done: int, total: int) -> None:
        print(f"  embedding training set: {done}/{total}", flush=True)

    from zotero_summarizer.services._common import read_config
    goals_config = read_config(settings.config_path)

    predictions, thresholds = classifier.predict_new_items(
        training_rows=training_rows,
        new_items=new_items,
        corpus_db_path=settings.corpus_db_path,
        classifier_name=args.classifier,
        pca_dim=args.pca_dim,
        n_folds=args.folds,
        calibration=args.calibration,
        threshold_strategy=args.threshold_strategy,
        goals_config=goals_config,
        progress_cb=_progress,
    )

    output_csv = Path(args.output or settings.project_root / f"feed-predictions-{args.feeds or 'all'}.csv")
    classifier.write_feed_predictions_csv(predictions, output_csv)

    md = classifier.format_feed_predictions_markdown(predictions, thresholds)
    print(md)
    print()
    print(f"saved annotation CSV: {output_csv}")
    return 0


def _goldenset_classify(args: argparse.Namespace) -> int:
    """Train SPECTER2 + classifier on the golden labels and score every row via CV.

    Writes per-classifier columns to the golden CSV (never overwrites another
    classifier's data) and appends a JSONL line to ``classifier-runs.jsonl``
    with the full config + metrics snapshot.
    """
    import csv as _csv

    from zotero_summarizer.services import classifier, run_log
    from zotero_summarizer.services._common import read_config

    settings = Settings.load(project_root=args.project_root)
    input_csv = Path(args.input or settings.project_root / "zotero-summarizer-golden.csv")
    if not input_csv.exists():
        raise FileNotFoundError(f"Golden CSV not found at {input_csv}; run `goldenset export` first.")

    with input_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(_csv.DictReader(f))

    def _progress(done: int, total: int) -> None:
        print(f"  embedded {done}/{total}", flush=True)

    goals_config = read_config(settings.config_path)
    report = classifier.cross_validate(
        rows,
        corpus_db_path=settings.corpus_db_path,
        n_folds=args.folds,
        classifier_name=args.classifier,
        pca_dim=args.pca_dim,
        holdout_fraction=args.holdout_fraction,
        calibration=args.calibration,
        threshold_strategy=args.threshold_strategy,
        goals_config=goals_config,
        progress_cb=_progress,
    )
    updated = classifier.write_predictions_to_csv(
        input_csv, report, classifier_name=args.classifier,
    )

    strength_filter = (
        {s.strip() for s in args.strength.split(",") if s.strip()}
        if args.strength
        else None
    )
    priority_col = f"cls_{args.classifier}_priority"
    metrics_cv = classifier.compute_metrics_against_gold(
        input_csv, strength_filter=strength_filter, split="cv",
        priority_column=priority_col,
    )
    metrics_holdout = classifier.compute_metrics_against_gold(
        input_csv, strength_filter=strength_filter, split="holdout",
        priority_column=priority_col,
    )

    output = {
        "run_id": run_log.make_run_id(args.classifier),
        "timestamp": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "classifier": args.classifier,
        "calibration": args.calibration,
        "threshold_strategy": args.threshold_strategy,
        "config": {
            "n_folds": args.folds,
            "pca_dim": args.pca_dim,
            "holdout_fraction": args.holdout_fraction,
            "strength_filter": sorted(strength_filter) if strength_filter else None,
        },
        "thresholds": {
            "keep": round(report.optimal_threshold, 4),
            "must": round(report.must_threshold, 4),
            "could": round(report.could_threshold, 4),
        },
        "cv": {
            "n_rows": report.n_rows,
            "n_positive": report.n_positive,
            "auc": round(report.auc, 4),
            "metrics_vs_gold": metrics_cv,
        },
        "holdout": {
            "n_rows": report.holdout_n_rows,
            "n_positive": report.holdout_n_positive,
            "auc": round(report.holdout_auc, 4),
            "metrics_vs_gold": metrics_holdout,
        },
        "embeddings_computed": report.embeddings_computed,
        "embeddings_cached": report.embeddings_cached,
        "elapsed_seconds": round(report.elapsed_seconds, 1),
        "csv_updated_rows": updated,
        "input_csv": str(input_csv),
        "input_csv_sha256_prefix": run_log.file_sha256(input_csv),
    }
    _persist_run_log(settings, output)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def _utc_iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _persist_run_log(settings: Settings, entry: dict) -> None:
    """Append the run entry to classifier-runs.jsonl + write a markdown report.

    Mutates ``entry`` in place to include the on-disk paths so the caller's
    JSON output points at the persisted artefacts.
    """
    from zotero_summarizer.services import run_log

    log_path = settings.project_root / "classifier-runs.jsonl"
    run_log.append_run(log_path, entry)
    reports_dir = settings.project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{entry['run_id']}.md"
    report_path.write_text(_format_run_report_md(entry), encoding="utf-8")
    entry["run_log_path"] = str(log_path)
    entry["report_path"] = str(report_path)


def _format_run_report_md(entry: dict) -> str:
    lines = [
        f"# {entry['run_id']}",
        "",
        f"- **classifier**: `{entry['classifier']}`",
        f"- **timestamp**: {entry['timestamp']}",
        f"- **git commit**: `{entry.get('git_commit') or '(no commit)'}`",
        f"- **input CSV**: `{entry['input_csv']}` (sha256={entry.get('input_csv_sha256_prefix', '')})",
        f"- **config**: {entry.get('config', {})}",
        f"- **thresholds**: {entry.get('thresholds', {})}",
        "",
    ]
    for split_name in ("cv", "holdout"):
        block = entry.get(split_name) or {}
        if not block:
            continue
        lines.append(f"## {split_name.upper()}")
        lines.append("")
        lines.append(f"- AUC: **{block.get('auc')}** · n={block.get('n_rows')} · positives={block.get('n_positive')}")
        m = block.get("metrics_vs_gold") or {}
        if m.get("total", 0) > 0:
            b = m.get("binary", {})
            lines.append(
                f"- binary keep: P=**{b.get('precision')}** R=**{b.get('recall')}** "
                f"F1=**{b.get('f1')}** (support={b.get('support')})"
            )
            pc = m.get("per_class", {}).get("must_read", {})
            lines.append(
                f"- must_read: P={pc.get('precision')} R={pc.get('recall')} F1={pc.get('f1')} "
                f"(support={pc.get('support')})"
            )
        lines.append("")
    return "\n".join(lines)


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

    # --- goldenset export -------------------------------------------------
    goldenset = subparsers.add_parser(
        "goldenset",
        help="Export a golden-label dataset from existing Zotero engagement signals",
    )
    gs_sub = goldenset.add_subparsers(dest="goldenset_command", required=True)
    gs_export = gs_sub.add_parser(
        "export",
        help=(
            "Pull every library item with an engagement signal (🧠/👀/👎/notes/"
            "annotations/trash) and write CSV + JSONL with inferred gold labels."
        ),
    )
    gs_export.add_argument(
        "--output-dir",
        default=None,
        help="Directory for golden CSV/JSONL. Default: project root.",
    )
    gs_export.add_argument(
        "--abstract-chars",
        type=int,
        default=1000,
        help="Truncate abstract to this many characters (0 = full).",
    )
    gs_export.add_argument("--project-root", default=None)
    gs_export.set_defaults(func=_goldenset_export)

    gs_classify = gs_sub.add_parser(
        "classify",
        help=(
            "SPECTER2 + logistic-regression classifier on the golden labels "
            "(5-fold CV). Replaces LLM-from-abstract scoring for ranking."
        ),
    )
    gs_classify.add_argument(
        "--input",
        default=None,
        help="Path to golden CSV. Default: <project-root>/zotero-summarizer-golden.csv",
    )
    gs_classify.add_argument(
        "--folds",
        type=int,
        default=5,
        help="Stratified k-fold count. Default: 5.",
    )
    gs_classify.add_argument(
        "--classifier",
        choices=["logreg", "tabpfn", "lightgbm"],
        default="logreg",
        help=(
            "Which classifier to train per fold. 'logreg' = sklearn "
            "LogisticRegression on full 773-d features (fast, default). "
            "'lightgbm' = LightGBM gradient-boosted trees on same features. "
            "'tabpfn' = TabPFN-v2 transformer (~600MB first call, requires "
            "TABPFN_TOKEN); PCA-reduces embedding to --pca-dim first."
        ),
    )
    gs_classify.add_argument(
        "--pca-dim",
        type=int,
        default=100,
        help="PCA target dim for the SPECTER2 embedding when --classifier=tabpfn. Default: 100.",
    )
    gs_classify.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.20,
        help=(
            "Stratified fraction held out of CV for the final test set. "
            "Held-out rows are NEVER seen during training/calibration/threshold "
            "tuning — they give an honest estimate of deployment performance. "
            "0.0 disables. Default: 0.20."
        ),
    )
    gs_classify.add_argument(
        "--calibration",
        choices=["isotonic", "sigmoid", "none"],
        default="isotonic",
        help=(
            "Probability calibration applied per fold. 'isotonic' (default) "
            "is non-parametric. 'sigmoid' is Platt scaling. 'none' uses raw."
        ),
    )
    gs_classify.add_argument(
        "--threshold-strategy",
        choices=["youden", "f1"],
        default="youden",
        help=(
            "How to pick the binary keep/skip threshold from OOF probabilities. "
            "'youden' maximises TPR-FPR (balanced). 'f1' maximises F1 (recall-leaning)."
        ),
    )
    gs_classify.add_argument(
        "--strength",
        default=None,
        help=(
            "Comma-separated gold_signal_strength filter applied to metrics "
            "(does NOT change the training set). Default: all."
        ),
    )
    gs_classify.add_argument("--project-root", default=None)
    gs_classify.set_defaults(func=_goldenset_classify)

    gs_pred = gs_sub.add_parser(
        "predict-feed",
        help=(
            "Train the classifier on the golden CSV and predict 4-class priority "
            "for unread items from a specified feed. Writes an annotation CSV "
            "with an empty `your_label` column for human review."
        ),
    )
    gs_pred.add_argument(
        "--feeds",
        default=None,
        help="Feed name substring or numeric ID. Comma-separated. Default: all feeds.",
    )
    gs_pred.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of unread items to predict on. Default: 10.",
    )
    gs_pred.add_argument(
        "--classifier",
        choices=["logreg", "tabpfn", "lightgbm"],
        default="tabpfn",
        help="Classifier to train on the golden CSV. Default: tabpfn (best AUC).",
    )
    gs_pred.add_argument(
        "--folds",
        type=int,
        default=5,
        help="Stratified k-fold count used to fit the calibrator + tune thresholds. Default: 5.",
    )
    gs_pred.add_argument(
        "--pca-dim",
        type=int,
        default=100,
        help="PCA target dim for SPECTER2 embedding when --classifier=tabpfn. Default: 100.",
    )
    gs_pred.add_argument(
        "--calibration",
        choices=["isotonic", "sigmoid", "none"],
        default="isotonic",
    )
    gs_pred.add_argument(
        "--threshold-strategy",
        choices=["youden", "f1"],
        default="youden",
    )
    gs_pred.add_argument(
        "--golden-csv",
        default=None,
        help="Path to training CSV. Default: <project-root>/zotero-summarizer-golden.csv",
    )
    gs_pred.add_argument(
        "--output",
        default=None,
        help="Annotation CSV destination. Default: <project-root>/feed-predictions-<feed>.csv",
    )
    gs_pred.add_argument("--project-root", default=None)
    gs_pred.set_defaults(func=_goldenset_predict_feed)

    gs_notes = gs_sub.add_parser(
        "analyze-notes",
        help=(
            "Pull user-written Zotero notes, send each to the LLM for a "
            "must/should/could/dont verdict, write a review CSV with empty "
            "`your_label` defaulted to the LLM's choice. LLM-generated and "
            "compilation notes get SKIP."
        ),
    )
    gs_notes.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N candidate notes. Default: all surviving the filter.",
    )
    gs_notes.add_argument(
        "--min-chars",
        type=int,
        default=100,
        help="Skip notes shorter than this. Default: 100.",
    )
    gs_notes.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Skip notes longer than this (compilations / imported essays). Default: 4000.",
    )
    gs_notes.add_argument(
        "--model",
        default=None,
        help=(
            "Override LLM model for this run. Default: goals.yaml refine_model. "
            "Example for OpenRouter Nemotron Super: "
            "'nvidia/llama-3.3-nemotron-super-49b-v1.5:free'."
        ),
    )
    gs_notes.add_argument(
        "--api-base",
        default=None,
        help=(
            "Override OpenAI-compatible endpoint for this run. Default: "
            "goals.yaml llm.api_base. Example: 'https://openrouter.ai/api/v1'."
        ),
    )
    gs_notes.add_argument(
        "--api-key-env",
        default=None,
        help=(
            "Env var name holding the API key. Default: goals.yaml "
            "llm.api_key_env. Use 'OPENROUTER_API_KEY' to point at OpenRouter."
        ),
    )
    gs_notes.add_argument(
        "--output",
        default=None,
        help="Review CSV destination. Default: <project-root>/note-analyses.csv",
    )
    gs_notes.add_argument("--project-root", default=None)
    gs_notes.set_defaults(func=_goldenset_analyze_notes)

    gs_cls_llm = gs_sub.add_parser(
        "classify-llm",
        help=(
            "4th classifier in the lineup: LLM reads title+abstract and "
            "classifies directly (single prompt per paper, parallel batch). "
            "Comparable to LogReg / LightGBM / TabPFN. Override the endpoint "
            "with --api-base + --api-key-env + --model to test any "
            "OpenAI-compatible model (e.g. api.kather.ai)."
        ),
    )
    gs_cls_llm.add_argument(
        "--input",
        default=None,
        help="Golden CSV. Default: <project-root>/zotero-summarizer-golden.csv",
    )
    gs_cls_llm.add_argument(
        "--strength",
        default=None,
        help="Comma-separated gold_signal_strength filter (high|medium|low). Default: all.",
    )
    gs_cls_llm.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on rows processed (post-strength filter). Default: all.",
    )
    gs_cls_llm.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel LLM-call workers (ThreadPoolExecutor). Default: 4.",
    )
    gs_cls_llm.add_argument(
        "--model",
        default=None,
        help=(
            "Override LLM model. Default: goals.yaml refine_model. "
            "For api.kather.ai pass the model id exposed by that endpoint."
        ),
    )
    gs_cls_llm.add_argument(
        "--api-base",
        default=None,
        help=(
            "Override OpenAI-compatible endpoint. Default: goals.yaml api_base. "
            "For api.kather.ai use 'https://api.kather.ai/v1'."
        ),
    )
    gs_cls_llm.add_argument(
        "--api-key-env",
        default="CUSTOM_API_KEY",
        help=(
            "Env var holding the API key. Default: 'CUSTOM_API_KEY' "
            "(add it to .env first). Use 'OPENROUTER_API_KEY' for OpenRouter, "
            "or 'OPENAI_API_KEY' for the default goals.yaml provider."
        ),
    )
    gs_cls_llm.add_argument(
        "--classifier-name",
        default=None,
        help=(
            "Short slug used as the CSV column prefix and run_log identifier. "
            "Default: auto-derived from the model name (e.g. 'llm_nvidia_nemotron_3_super')."
        ),
    )
    gs_cls_llm.add_argument("--project-root", default=None)
    gs_cls_llm.set_defaults(func=_goldenset_classify_llm)

    gs_train = gs_sub.add_parser(
        "train-classifier",
        help=(
            "Train a classifier on the golden CSV and persist it to "
            "~/.cache/zotero-summarizer/models/. The daemon's hybrid gate "
            "loads this artifact at startup. Pure model output: no CV-row "
            "writeback to the golden CSV."
        ),
    )
    gs_train.add_argument(
        "--classifier",
        choices=["tabpfn", "lightgbm", "logreg"],
        default="tabpfn",
        help="Which classifier to train. Default: tabpfn (best F1).",
    )
    gs_train.add_argument(
        "--folds",
        type=int,
        default=5,
        help="K-fold count for OOF calibration + threshold tuning. Default: 5.",
    )
    gs_train.add_argument(
        "--pca-dim",
        type=int,
        default=100,
        help="PCA target for SPECTER2 embedding when classifier=tabpfn. Default: 100.",
    )
    gs_train.add_argument(
        "--input",
        default=None,
        help="Path to golden CSV. Default: <project-root>/zotero-summarizer-golden.csv",
    )
    gs_train.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory for model artefact. Default: ~/.cache/zotero-summarizer/models/. "
            "Two files written: <classifier>.joblib + <classifier>.json (metadata)."
        ),
    )
    gs_train.add_argument(
        "--force",
        action="store_true",
        help="Retrain even when the cached model's golden sha matches the current CSV.",
    )
    gs_train.add_argument("--project-root", default=None)
    gs_train.set_defaults(func=_goldenset_train_classifier)

    gs_eval_baseline = gs_sub.add_parser(
        "eval-baseline",
        help=(
            "Phase 1.16 Step 0 — measure the current model's true performance "
            "with proper uncertainty (5x5 repeated stratified K-fold CV + BCa "
            "bootstrap CIs). Computes Spearman, AUC, NDCG@10, MAE, Cohen's "
            "kappa. No model changes. Writes eval-baseline-<ts>.json."
        ),
    )
    gs_eval_baseline.add_argument(
        "--classifier",
        choices=["tabpfn", "lightgbm", "logreg"],
        default="lightgbm",
        help="Which classifier to evaluate. Default: lightgbm (current default).",
    )
    gs_eval_baseline.add_argument(
        "--input",
        default=None,
        help="Path to golden CSV. Default: <project-root>/zotero-summarizer-golden.csv",
    )
    gs_eval_baseline.add_argument(
        "--output",
        default=None,
        help="JSON output path. Default: eval-baseline-<timestamp>.json in project root.",
    )
    gs_eval_baseline.add_argument(
        "--n-repeats", type=int, default=5,
        help="Number of CV repeats (each does n-folds folds). Default: 5.",
    )
    gs_eval_baseline.add_argument(
        "--n-folds", type=int, default=5,
        help="Folds per repeat. Default: 5.",
    )
    gs_eval_baseline.add_argument(
        "--n-bootstrap", type=int, default=2000,
        help="Bootstrap iterations for BCa CIs. Default: 2000.",
    )
    gs_eval_baseline.add_argument(
        "--pca-dim", type=int, default=100,
        help="PCA target for SPECTER2 (TabPFN only). Default: 100.",
    )
    gs_eval_baseline.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility. Default: 42.",
    )
    gs_eval_baseline.add_argument(
        "--learning-curve", action="store_true",
        help=(
            "Run the learning-curve sweep instead of full repeated CV. "
            "Subsamples training set at 15/30/60/85/100 percent and reports "
            "Spearman + NDCG@10 with BCa CIs at each. Writes "
            "learning-curve-<ts>.json."
        ),
    )
    gs_eval_baseline.add_argument(
        "--learning-curve-fractions",
        default=None,
        help=(
            "Comma-separated ascending fractions in (0, 1]. Default: "
            "0.15,0.30,0.60,0.85,1.00. Only meaningful with --learning-curve."
        ),
    )
    gs_eval_baseline.add_argument("--project-root", default=None)
    gs_eval_baseline.set_defaults(func=_goldenset_eval_baseline)

    gs_compare = gs_sub.add_parser(
        "compare",
        help=(
            "Print a side-by-side table of every classifier's latest run from "
            "classifier-runs.jsonl. No re-training — pure replay from disk."
        ),
    )
    gs_compare.add_argument(
        "--log",
        default=None,
        help="Path to run log. Default: <project-root>/classifier-runs.jsonl",
    )
    gs_compare.add_argument(
        "--split",
        choices=["cv", "holdout"],
        default="cv",
        help="Which metric block to display. Default: cv.",
    )
    gs_compare.add_argument("--project-root", default=None)
    gs_compare.set_defaults(func=_goldenset_compare)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
