"""One persisted, structured readiness checklist shared by web and CLI."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import shutil
import sqlite3
from threading import Lock
from typing import Any, Callable

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.models.providers import STAGES, resolve_stage
from zotero_summarizer.services._common import (
    read_config,
    read_json_or_empty,
    write_json_atomic,
)
from zotero_summarizer.services.setup.doctor_environment import (
    _database,
    _environment,
    _local_profile,
    _research_goals,
    _row,
    _zotero,
)
from zotero_summarizer.settings import Settings, offline_requested

_CHECKS = (
    "environment",
    "research_goals",
    "local_profile",
    "zotero",
    "database",
    "runtime_model",
    "llm_inference",
    "ml_assets",
    "rss_source",
    "dry_run",
    "optional_extras",
)
_REQUIRED = frozenset(_CHECKS) - {"local_profile", "optional_extras"}
_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_model(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.llm.model_list import list_models_for_provider

    config = read_config(settings.config_path, settings.calibration_path)
    if not config.llm_enabled:
        return _row(
            "runtime_model",
            "ready",
            "AI runtime check skipped by choice",
            "ML-only triage remains available.",
        )
    resolved = [resolve_stage(config.llm_routing, stage) for stage in STAGES]
    local = [item for item in resolved if item.provider.is_local]
    uses_ollama = local and any(
        "localhost:11434" in (item.provider.base_url or "") for item in local
    )
    if uses_ollama and shutil.which("ollama") is None:
        return _row(
            "runtime_model",
            "needs_action",
            "Ollama is not installed",
            "The selected profile uses localhost:11434.",
            action="Install Ollama",
            command="brew install ollama",
        )
    served: dict[str, list[str]] = {}
    for item in resolved:
        if item.provider.name not in served:
            try:
                served[item.provider.name] = list_models_for_provider(item.provider)
            except Exception as exc:
                if item.provider.is_local and uses_ollama:
                    return _row(
                        "runtime_model",
                        "needs_action",
                        "Ollama is not running",
                        f"{type(exc).__name__}: {exc}",
                        action="Start Ollama",
                        command="ollama serve",
                    )
                raise
    missing = [
        item for item in resolved if item.model not in served[item.provider.name]
    ]
    if missing:
        item = missing[0]
        command = (
            f"ollama pull {item.model}"
            if item.provider.is_local and uses_ollama
            else None
        )
        return _row(
            "runtime_model",
            "needs_action",
            "Configured model is not available",
            ", ".join(f"{row.provider.name}/{row.model}" for row in missing),
            action="Download model" if command else "Choose a served model",
            command=command,
        )
    identities = ", ".join(
        sorted({f"{item.provider.name}/{item.model}" for item in resolved})
    )
    return _row(
        "runtime_model", "ready", "Runtime serves every configured model", identities
    )


def _llm_inference(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.llm.operational_check import check_routing_stages

    config = read_config(settings.config_path, settings.calibration_path)
    if not config.llm_enabled:
        return _row(
            "llm_inference",
            "ready",
            "AI inference check skipped by choice",
            "Enable AI later in Settings to run model-backed features.",
        )
    result = asyncio.run(check_routing_stages(config.llm_routing))
    failed = [row for row in result["stages"] if row["status"] != "operational"]
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for row in result["stages"]:
        key = (row["provider"], row["model"], row.get("detail") or row["status"])
        grouped.setdefault(key, []).append(row["stage"])
    detail = "; ".join(
        f"{provider}/{model} ({', '.join(stages)}): {outcome}"
        for (provider, model, outcome), stages in grouped.items()
    )
    providers = {row.name: row for row in config.llm_routing.providers}
    stale = next(
        (
            row
            for row in failed
            if providers[row["provider"]].is_local
            and "does not support chat" in row.get("detail", "")
        ),
        None,
    )
    return _row(
        "llm_inference",
        "needs_action" if failed else "ready",
        "Inference failed for a configured stage"
        if failed
        else "Every LLM stage returned a real response",
        detail,
        action="Refresh model" if stale else "Retry inference",
        command=f"ollama pull {stale['model']}" if stale else None,
    )


def _ml_assets(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.setup.assets import offline_asset_report

    report = offline_asset_report(settings)
    missing = [row["repo_id"] for row in report["models"] if not row["cached"]]
    ok = report["offline_ready"] and report["loadable"]
    return _row(
        "ml_assets",
        "ready" if ok else "needs_action",
        "ML assets load in cache-only mode" if ok else "ML assets are incomplete",
        ", ".join(missing) or f"{len(report['models'])} models loaded offline",
        action="Prefetch ML assets",
        command="uv run zotero-summarizer prefetch-models",
    )


def _rss_source(settings: Settings) -> dict[str, Any]:
    with sqlite3.connect(
        f"file:{settings.triage_db_path}?mode=ro",
        uri=True,
        timeout=10,
    ) as conn:
        count = int(
            conn.execute("SELECT count(*) FROM rss_feeds WHERE enabled = 1").fetchone()[
                0
            ]
        )
    return _row(
        "rss_source",
        "ready" if count else "needs_action",
        f"{count} RSS source{'s' if count != 1 else ''} enabled"
        if count
        else "No RSS source is enabled",
        "Today needs at least one source.",
        action="Add a source in Settings",
    )


def _dry_run(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.llm.factory import build_client_for_stage
    from zotero_summarizer.services.triage.summarization import run_abstract_dry

    config = read_config(settings.config_path, settings.calibration_path)
    if not config.llm_enabled:
        return _row(
            "dry_run",
            "ready",
            "LLM dry-run skipped by choice",
            "The classifier-only backlog path does not call an LLM.",
        )
    resolved = resolve_stage(config.llm_routing, "feed")
    request = SummarizeRequest(
        title="Dry-run paper: reproducible local research triage",
        abstract=(
            "We evaluate a reproducible paper-triage method with held-out data, "
            "ablation studies, confidence intervals, and public code."
        ),
    )
    result = run_abstract_dry(config, request, build_client_for_stage(resolved))
    detail = f"priority={result.reading_priority}; score={result.relevance_score}"
    return _row(
        "dry_run", "ready", "Dry-run triage completed without a Zotero write", detail
    )


def _optional_extras(_: Settings) -> dict[str, Any]:
    browsers = importlib.util.find_spec("patchright") is not None
    return _row(
        "optional_extras",
        "ready" if browsers else "unavailable",
        "Browser/PDF extras are available"
        if browsers
        else "Browser automation is optional and unavailable",
        "Core local triage does not require browser automation.",
        action="Install browser extras",
        command=None if browsers else "uv sync --extra browser",
    )


_RUNNERS: dict[str, Callable[[Settings], dict[str, Any]]] = {
    "environment": _environment,
    "research_goals": _research_goals,
    "local_profile": _local_profile,
    "zotero": _zotero,
    "database": _database,
    "runtime_model": _runtime_model,
    "llm_inference": _llm_inference,
    "ml_assets": _ml_assets,
    "rss_source": _rss_source,
    "dry_run": _dry_run,
    "optional_extras": _optional_extras,
}


def _save(settings: Settings, payload: dict[str, Any]) -> None:
    write_json_atomic(settings.data_dir / "setup_doctor.json", payload)


def doctor_status(settings: Settings) -> dict[str, Any]:
    path = settings.data_dir / "setup_doctor.json"
    payload = read_json_or_empty(path)
    if not payload:
        return {
            "status": "not_started",
            "ready": False,
            "checks": [
                _row(check_id, "not_started", "Not checked yet") for check_id in _CHECKS
            ],
            "modes": {},
            "last_successful_at": None,
        }
    by_id = {row["id"]: row for row in payload.get("checks", [])}
    payload["checks"] = [
        by_id.get(check_id, _row(check_id, "not_started", "Not checked yet"))
        for check_id in _CHECKS
    ]
    if not _LOCK.locked():
        for row in payload.get("checks", []):
            if row.get("status") == "running":
                row.update(
                    status="needs_action", message="Previous check was interrupted"
                )
    return payload


def _modes(settings: Settings, rows: list[dict[str, Any]]) -> dict[str, str]:
    try:
        config = read_config(settings.config_path, settings.calibration_path)
        no_llm = not config.llm_enabled
        local = all(
            resolve_stage(config.llm_routing, stage).provider.is_local
            for stage in STAGES
        )
    except Exception:  # noqa: BLE001 — invalid config is a diagnostic result, not a crash
        no_llm = False
        local = False
    assets = next(row for row in rows if row["id"] == "ml_assets")["status"] == "ready"
    inference = (
        next(row for row in rows if row["id"] == "llm_inference")["status"] == "ready"
    )
    local_ready = local and inference
    strict = offline_requested()
    if no_llm:
        return {
            "local_inference": "unavailable",
            "offline_ready": "ready" if assets else "needs_action",
            "strict_offline": "ready"
            if strict and assets
            else ("needs_action" if strict else "not_started"),
        }
    return {
        "local_inference": "ready"
        if local_ready
        else ("needs_action" if local else "unavailable"),
        "offline_ready": "ready" if local_ready and assets else "needs_action",
        "strict_offline": "ready"
        if strict and local_ready and assets
        else ("needs_action" if strict else "not_started"),
    }


def _redact_details(settings: Settings, rows: list[dict[str, Any]]) -> None:
    from zotero_summarizer.services.llm.credentials import get_api_key

    try:
        providers = read_config(
            settings.config_path, settings.calibration_path
        ).llm_routing.providers
        secrets = [
            resolved[0]
            for provider in providers
            if (resolved := get_api_key(provider.api_key_env))
        ]
    except Exception:  # noqa: BLE001 — redaction must not break the diagnostic
        secrets = []
    for row in rows:
        detail = str(row.get("detail") or "")
        for secret in secrets:
            detail = detail.replace(secret, "[redacted]")
        row["detail"] = detail


def run_doctor(
    settings: Settings,
    *,
    check_ids: list[str] | None = None,
    fix: bool = False,
) -> dict[str, Any]:
    selected = list(dict.fromkeys(check_ids or _CHECKS))
    unknown = sorted(set(selected) - set(_CHECKS))
    if unknown:
        raise APIError(
            "unknown_doctor_check", f"Unknown check IDs: {unknown}", status_code=422
        )
    if not _LOCK.acquire(blocking=False):
        raise APIError(
            "doctor_running", "Setup checks are already running", status_code=409
        )
    try:
        if fix:
            from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
            from zotero_summarizer.storage.migrations import migrate_existing

            bootstrap_phase0(settings)
            migrate_existing(settings)
        current = doctor_status(settings)
        by_id = {row["id"]: row for row in current["checks"]}
        for row in by_id.values():
            if row["status"] == "running":
                row.update(
                    status="needs_action", message="Previous check was interrupted"
                )
        for check_id in selected:
            by_id[check_id] = _row(check_id, "running", "Check is running")
        running = {
            **current,
            "status": "running",
            "checks": [by_id[key] for key in _CHECKS],
            "started_at": _now(),
        }
        _save(settings, running)
        for check_id in selected:
            try:
                by_id[check_id] = _RUNNERS[check_id](settings)
            except Exception as exc:  # noqa: BLE001 — diagnostic status boundary
                by_id[check_id] = _row(
                    check_id,
                    "needs_action",
                    f"{check_id.replace('_', ' ').title()} failed",
                    f"{type(exc).__name__}: {exc}",
                )
        rows = [by_id[key] for key in _CHECKS]
        _redact_details(settings, rows)
        ready = all(row["status"] == "ready" for row in rows if row["id"] in _REQUIRED)
        payload = {
            "status": "ready" if ready else "needs_action",
            "ready": ready,
            "checks": rows,
            "modes": _modes(settings, rows),
            "finished_at": _now(),
            "last_successful_at": _now()
            if ready
            else current.get("last_successful_at"),
        }
        _save(settings, payload)
        return payload
    finally:
        _LOCK.release()
