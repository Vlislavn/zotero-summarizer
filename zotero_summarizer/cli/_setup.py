"""Setup, doctor and calibration CLI front-ends over shared services."""
from __future__ import annotations

import argparse

from zotero_summarizer.settings import Settings


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def _confirm(label: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} ({hint}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _step_paths(settings: Settings) -> None:
    from zotero_summarizer.services.setup import detect_zotero_data_dirs, write_env_paths

    print("\n== Zotero library ==")
    candidates = detect_zotero_data_dirs()
    for idx, cand in enumerate(candidates):
        flag = "DB found" if cand.db_exists else "no zotero.sqlite"
        print(f"  [{idx}] {cand.data_dir}  ({flag}, source={cand.source})")

    chosen = ""
    if candidates:
        pick = _prompt("Pick a Zotero data dir by number, or type a path", "0")
        if pick.isdigit() and int(pick) < len(candidates):
            chosen = candidates[int(pick)].data_dir
        else:
            chosen = pick
    else:
        chosen = _prompt("Zotero data dir path", "")

    pdf_root = _prompt("PDF storage root (PDF_ROOT)", str(settings.pdf_root))

    updates: dict[str, str] = {}
    if chosen:
        updates["ZOTERO_DATA_DIR"] = chosen
    if pdf_root:
        updates["PDF_ROOT"] = pdf_root
    if not updates:
        print("  (no paths entered — leaving .env unchanged)")
        return

    from zotero_summarizer.api.errors import APIError

    try:
        result = write_env_paths(settings.env_path, updates)
    except APIError as exc:
        print(f"  ! {exc.message}")
        print("  Fix the path and re-run `zotero-summarizer setup`.")
        return
    print(f"  wrote {result.written} to {settings.env_path} (restart to apply)")


def _step_provider() -> None:
    import os

    from zotero_summarizer.models.providers import ProviderConfig, ProviderType
    from zotero_summarizer.services.llm import operational_check

    print("\n== LLM provider (reachability test) ==")
    print("  (the API key is read from an ENV VAR you name below — never typed here)")
    type_raw = _prompt("Provider type (openai|anthropic)", "openai").lower()
    provider_type = ProviderType.anthropic if type_raw == "anthropic" else ProviderType.openai
    base_url = ""
    if provider_type is ProviderType.openai:
        base_url = _prompt("Base URL (OpenAI-compatible /v1)", "http://localhost:11434/v1")
    api_key_env = _prompt("Env var NAME holding the API key", "OPENAI_API_KEY")
    model = _prompt("Model id to test", "gpt-oss:20b")

    if not os.getenv(api_key_env, "").strip():
        print(f"  ! {api_key_env} is not set in this shell/.env — set it before the test passes.")

    provider = ProviderConfig(
        name="setup-test",
        type=provider_type,
        base_url=base_url or None,
        api_key_env=api_key_env,
    )
    if not _confirm("Run the reachability probe now?", default=True):
        return
    print("  probing…")
    result = operational_check.probe_provider(provider, model)
    status = result["status"]
    detail = result["detail"]
    print(f"  → {status}" + (f": {detail}" if detail else ""))


def _step_goals(settings: Settings) -> None:
    from zotero_summarizer.services._common import read_config, write_user_config
    from zotero_summarizer.services.setup.validate import has_personal_goals

    print("\n== Research goals ==")
    config = read_config(settings.config_path)
    current = list(config.research_goals or [])
    personal = has_personal_goals(config)
    if personal:
        print("  current goals:")
        for goal in current:
            print(f"    - {goal}")
    if not _confirm("Replace the research goals now?", default=not personal):
        return

    print("  Enter one research goal per line; blank line to finish.")
    goals: list[str] = []
    while True:
        line = input("  goal> ").strip()
        if not line:
            break
        goals.append(line)
    if not goals:
        print("  (no goals entered — leaving goals.yaml unchanged)")
        return

    config.research_goals = goals
    write_user_config(settings.config_path, config)
    print(f"  wrote {len(goals)} research goal(s) to {settings.config_path}")


def _setup(args: argparse.Namespace) -> int:
    settings = Settings.load(project_root=args.project_root)

    from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0

    result = bootstrap_phase0(settings)
    print("zotero-summarizer setup")
    print(f"  project root: {settings.project_root}")
    created = [name for name, flag in (
        ("goals.yaml", result.created_goals),
        (".env", result.created_env),
        ("triage DB", result.migrated_db),
    ) if flag]
    if created:
        print(f"  bootstrapped: {', '.join(created)}")

    local_result = None
    if args.mode == "local":
        from zotero_summarizer.services.setup.profiles import set_local_profile

        local_result = set_local_profile(
            settings, args.profile, endpoint=args.endpoint, model=args.model,
        )
        size = f" ({local_result['size_gb']} GB)" if local_result["size_gb"] else ""
        print(f"\n== Local model ==\n  {local_result['model']}{size} at {local_result['endpoint']}")
        if local_result["source"]:
            print(f"  {local_result['source']}")
        if local_result["pull_command"]:
            print("  Model download requires your explicit action:")
            print(f"    {local_result['pull_command']}")
    _step_paths(settings)
    if args.mode == "hosted":
        _step_provider()
    _step_goals(settings)

    print("\nConfiguration saved; setup is complete only when `zotero-summarizer doctor` reports Ready.")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    import json

    from zotero_summarizer.services.setup.doctor import run_doctor

    settings = Settings.load(project_root=args.project_root)
    selected = [value.strip() for value in (args.check or "").split(",") if value.strip()] or None
    result = run_doctor(settings, check_ids=selected, fix=args.fix)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        for row in result["checks"]:
            print(f"{row['status']:12} {row['id']:16} {row['message']}")
            if row["status"] == "needs_action" and row["recovery"].get("command"):
                print(f"  → {row['recovery']['command']}")
        print(f"\nSetup: {'Ready' if result['ready'] else 'Needs action'}")
    return 0 if result["ready"] else 1


def _calibrate(args: argparse.Namespace) -> int:
    import json

    from zotero_summarizer.services.setup.calibration import run_full_calibration, tier3_recalibrate

    settings = Settings.load(project_root=args.project_root)
    item_keys = [k.strip() for k in (args.item_keys or "").split(",") if k.strip()] or None
    print("Calibrating to your setup (Tier-1 env probe + Tier-2 budget sweep)…", flush=True)
    result = run_full_calibration(settings, item_keys=item_keys, papers_limit=args.papers)

    if args.tier3:
        from zotero_summarizer.services._common import load_golden_rows, read_config
        from zotero_summarizer.services.model.eval_baseline import run_baseline

        classifiers = [c.strip() for c in args.tier3_classifiers.split(",") if c.strip()]
        print(f"Tier-3: evaluating {classifiers} on your golden labels…", flush=True)

        def _classifier_eval(s: Settings) -> dict[str, dict[str, float]]:
            cfg = read_config(s.config_path, s.calibration_path)
            rows = load_golden_rows(s.golden_csv_path)
            scores: dict[str, dict[str, float]] = {}
            for clf in classifiers:
                report = run_baseline(rows, corpus_db_path=s.corpus_db_path, goals_config=cfg,
                                      classifier_name=clf, n_repeats=2, n_folds=5)
                scores[clf] = {"oof_spearman": report.spearman_rho.point, "auc": report.auc.point}
            return scores

        result["tier3"] = tier3_recalibrate(settings, run_eval=_classifier_eval, min_labels=args.tier3_min_labels)

    print(json.dumps(result, indent=2))
    print(f"\nWrote {settings.calibration_path} — applied automatically on next config load.")
    return 0


def register_setup(subparsers) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="Interactive first-run setup: Zotero dir, LLM provider, research goals",
    )
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--mode", choices=("local", "hosted", "no-llm"), default="hosted")
    parser.add_argument("--profile", choices=("light", "balanced", "existing"), default="light")
    parser.add_argument("--endpoint", default="http://localhost:11434/v1")
    parser.add_argument("--model", default=None, help="Model served by --profile existing")
    parser.set_defaults(func=_setup)

    doctor = subparsers.add_parser("doctor", help="Run the persisted setup/readiness checklist")
    doctor.add_argument("--project-root", default=None)
    doctor.add_argument("--json", dest="as_json", action="store_true")
    doctor.add_argument("--fix", action="store_true", help="Apply safe bootstrap/database fixes")
    doctor.add_argument("--check", default=None, help="Comma-separated check IDs to retry")
    doctor.set_defaults(func=_doctor)

    calib = subparsers.add_parser(
        "calibrate",
        help="Calibrate technical defaults to your setup (Tier-1 env probe + Tier-2 budget sweep)",
    )
    calib.add_argument("--project-root", default=None)
    calib.add_argument("--item-keys", default=None,
                       help="Comma-separated paper item keys to sweep (default: auto-pick built briefs)")
    calib.add_argument("--papers", type=int, default=3, help="How many papers to sweep (default 3)")
    calib.add_argument("--tier3", action="store_true",
                       help="Also run Tier-3 data-driven classifier recal on your golden labels (HEAVY — eval_baseline CV; run foreground)")
    calib.add_argument("--tier3-classifiers", default="lightgbm,logreg",
                       help="Classifiers to compare in Tier-3 (default lightgbm,logreg; add tabpfn if RAM allows)")
    calib.add_argument("--tier3-min-labels", type=int, default=200,
                       help="Skip Tier-3 below this many golden labels (Tier-0 default stands)")
    calib.set_defaults(func=_calibrate)
