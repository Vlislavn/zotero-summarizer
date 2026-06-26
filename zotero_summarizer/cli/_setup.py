"""``zotero-summarizer setup`` — interactive first-run terminal onboarding.

A guided flow that REUSES the ``services/setup`` primitives the HTTP layer uses
(no duplicated logic): bootstrap absent files, pick the Zotero data dir
(detect → confirm → ``write_env_paths``), configure the LLM provider (prompt the
provider profile → reachability test via ``operational_check.probe_provider``),
and set the research goals (persist via the shared ``write_config_atomic``).

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
    ``write_config_atomic`` primitive (the same one the config service uses)."""
    from zotero_summarizer.services._common import read_config, write_config_atomic

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
    write_config_atomic(settings.config_path, config.model_dump(mode="json"))
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


def register_setup(subparsers) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="Interactive first-run setup: Zotero dir, LLM provider, research goals",
    )
    parser.add_argument("--project-root", default=None)
    parser.set_defaults(func=_setup)
