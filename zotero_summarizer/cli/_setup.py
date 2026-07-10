"""``zotero-summarizer setup`` — interactive first-run terminal onboarding.

A guided flow that REUSES the ``services/setup`` primitives the HTTP layer uses
(no duplicated logic): bootstrap absent files, pick the Zotero data dir
(detect → confirm → ``write_env_paths``), configure the LLM provider (prompt the
provider profile → reachability test via ``operational_check.probe_provider``),
and set the research goals (persist via the shared ``write_user_config`` — only the
user-owned keys; technical knobs stay code defaults).

Everything writes through the same allowlisted/validated paths as the API, so the
CLI and the Settings UI can never drift.
"""
from __future__ import annotations

import argparse

from zotero_summarizer.settings import Settings


def _prompt(label: str, default: str = "") -> str:
    """Read one line; empty input keeps ``default``. Trims surrounding space."""
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
    """Detect candidate Zotero data dirs, let the user pick, write the path keys."""
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

    # write_env_paths raises APIError(422) on a non-existent path; surface it as a
    # clear message and let the user re-run rather than silently writing bad paths.
    from zotero_summarizer.api.errors import APIError

    try:
        result = write_env_paths(settings.env_path, updates)
    except APIError as exc:
        print(f"  ! {exc.message}")
        print("  Fix the path and re-run `zotero-summarizer setup`.")
        return
    print(f"  wrote {result.written} to {settings.env_path} (restart to apply)")


def _step_provider(settings: Settings) -> None:
    """Prompt the LLM provider profile (type / base_url / api_key_env NAME) and
    run a reachability test. Reads the key from the env var the user names — never
    prompts for the secret value itself."""
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
    """Prompt research goals and persist them into goals.yaml via the shared
    ``write_user_config`` primitive (the same one the config service uses) — only
    the user-owned keys are written; technical sections stay code defaults."""
    from zotero_summarizer.services._common import read_config, write_user_config

    print("\n== Research goals ==")
    config = read_config(settings.config_path)
    current = list(config.research_goals or [])
    if current:
        print("  current goals:")
        for goal in current:
            print(f"    - {goal}")
    if not _confirm("Replace the research goals now?", default=not current):
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

    # Phase 0: ensure goals.yaml/.env exist + the DB is migrated before prompting,
    # reusing the same bootstrap the server runs (idempotent, never overwrites).
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

    _step_paths(settings)
    _step_provider(settings)
    _step_goals(settings)

    print("\nDone. Restart the server (`zotero-summarizer serve`) to apply path changes.")
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    """`calibrate` — Tier-1 (env probe) + Tier-2 (text-budget sweep) on the resolved
    deep-review endpoint, persisting to data/calibration.json. FOREGROUND + watched (the
    memory-safe path): run it yourself in a terminal. Remote endpoints load
    no local model, so they're memory-safe regardless of swap."""
    import json

    from zotero_summarizer.services.setup.calibration import run_full_calibration, tier3_recalibrate

    settings = Settings.load(project_root=args.project_root)
    item_keys = [k.strip() for k in (args.item_keys or "").split(",") if k.strip()] or None
    print("Calibrating to your setup (Tier-1 env probe + Tier-2 budget sweep)…", flush=True)
    result = run_full_calibration(settings, item_keys=item_keys, papers_limit=args.papers)

    if args.tier3:
        # Tier-3: data-driven classifier recal on YOUR labels (heavy — eval_baseline CV;
        # run it foreground in a memory-safe window). Label-gated inside tier3_recalibrate.
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


def _profile(args: argparse.Namespace) -> int:
    """`profile` — pick a deployment profile (fully-local vs hybrid local+API), or measure
    which stages are token/compute-heavy across your providers to inform the choice."""
    import json

    from zotero_summarizer.services.setup.profiles import PROFILES, run_full_profile_measure, set_profile

    settings = Settings.load(project_root=args.project_root)
    if args.list:
        for name, profile in PROFILES.items():
            print(f"  {name:7} {profile['label']}\n           {profile['description']}\n")
        return 0
    if args.measure:
        print("Measuring stage costs across providers (remote always; local only with --include-local)…", flush=True)
        print(json.dumps(run_full_profile_measure(settings, include_local=args.include_local), indent=2))
        return 0
    if args.set:
        result = set_profile(settings, args.set, local_depth=args.local_depth)
        print(json.dumps(result, indent=2))
        print(f"\nApplied '{args.set}' to {settings.config_path}. Restart the daemon to pick up routing changes.")
        return 0
    print("nothing to do — pass --list, --measure, or --set {local|hybrid}")
    return 1


def register_setup(subparsers) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="Interactive first-run setup: Zotero dir, LLM provider, research goals",
    )
    parser.add_argument("--project-root", default=None)
    parser.set_defaults(func=_setup)

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

    prof = subparsers.add_parser(
        "profile",
        help="Deployment profile: fully-local vs hybrid (local+API) routing, or measure stage costs",
    )
    prof.add_argument("--project-root", default=None)
    prof.add_argument("--list", action="store_true", help="List the presets + their trade-offs")
    prof.add_argument("--measure", action="store_true",
                      help="Measure token/compute cost per stage×provider + recommend a profile")
    prof.add_argument("--include-local", action="store_true",
                      help="Also measure LOCAL gens (loads a multi-GB model — run in a memory-safe window)")
    prof.add_argument("--set", choices=sorted(("local", "hybrid")), help="Apply a preset")
    prof.add_argument("--local-depth", choices=("superficial", "deep"), default=None,
                      help="Override the local deep-review depth (deep = slow but thorough on local)")
    prof.set_defaults(func=_profile)
