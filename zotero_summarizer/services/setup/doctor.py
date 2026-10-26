"""One persisted, structured readiness checklist shared by web and CLI."""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import platform
import shutil
import sqlite3
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlsplit

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import STAGES, ProviderConfig, ProviderType, resolve_stage
from zotero_summarizer.services._common import (
    read_config,
    read_json_or_empty,
    state,
    write_json_atomic,
)
from zotero_summarizer.services.setup.doctor_environment import (
    _database,
    _environment,
    _local_profile,
    _optional_extras,
    _research_goals,
    _row,
    _restart_required,
    _saved_paths,
    _zotero,
    redact_diagnostics,
)
from zotero_summarizer.settings import Settings, offline_requested
from zotero_summarizer.services.setup.load_admission import (
    ADMISSION_REVISION, policy_configuration_signature, verification_row,
)
from zotero_summarizer.services.setup.load_attempt import SetupAttempt, checked_rows, current_attempt
from zotero_summarizer.services.setup.doctor_dry_run import _dry_run
from zotero_summarizer.services.setup.doctor_requests import (
    OWNERSHIP_REVISION, request_attempts, save_doctor, withhold_local_ready,
)

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
_REQUIRED = frozenset(_CHECKS) - {"local_profile", "optional_extras", "zotero"}
_LOCK = Lock()
_ACTIVE: SetupAttempt | None = None


def cancel_doctor(run_id: str) -> dict:
    attempt = _ACTIVE
    if attempt is None or attempt.run_id != run_id:
        raise APIError("doctor_not_running", "This verification is no longer running", status_code=409)
    receipt = attempt.abandon("cancelled")
    if receipt["finished"]:
        raise APIError("doctor_not_running", "Verification already finished", status_code=409)
    return {"ready": False, "status": "cancelling", "attempt": receipt}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ollama_endpoint(provider: ProviderConfig) -> bool:
    """The supported local OpenAI-compatible default port, not a sibling's runtime."""
    endpoint = provider.base_url or ""
    parsed = urlsplit(endpoint if "://" in endpoint else "http://" + endpoint)
    return provider.type == ProviderType.openai and provider.is_local and parsed.port == 11434


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
    served: dict[str, list[str]] = {}
    attempt = current_attempt()
    for item in resolved:
        if attempt:
            attempt.require_active()
        if item.provider.name in served:
            continue
        ollama = _ollama_endpoint(item.provider)
        identity = f"{item.provider.name}/{item.model} at {item.provider.base_url}"
        if ollama and shutil.which("ollama") is None:
            return _row("runtime_model", "needs_action", "Ollama is not installed",
                        f"{identity}. Install the runtime for your platform: https://ollama.com/download",
                        action="Install Ollama", command="brew install ollama" if platform.system() == "Darwin" else None)
        try:
            served[item.provider.name] = list_models_for_provider(item.provider)
        except Exception as exc:  # noqa: BLE001 — inventory failures retain the failing route
            row = _row(
                "runtime_model", "needs_action",
                "Ollama is not running" if ollama else f"Endpoint for {item.provider.name} is unavailable",
                f"{identity}: {type(exc).__name__}: {exc}",
                action="Start Ollama" if ollama else "Review endpoint settings",
                command="ollama serve" if ollama else None,
            )
            if not ollama:
                row["recovery"]["action"] = "open_ai_settings"
            return row
    missing = [
        item for item in resolved if item.model not in served[item.provider.name]
    ]
    if missing:
        item = missing[0]
        command = f"ollama pull {item.model}" if _ollama_endpoint(item.provider) else None
        row = _row(
            "runtime_model", "needs_action", "Configured model is not available",
            ", ".join(f"{row.provider.name}/{row.model} at {row.provider.base_url}" for row in missing),
            action="Download model" if command else f"Choose a served model for {item.provider.name}",
            command=command,
        )
        if not command:
            row["recovery"]["action"] = "open_ai_settings"
        return row
    identities = ", ".join(
        sorted({f"{item.provider.name}/{item.model}" for item in resolved})
    )
    return _row(
        "runtime_model", "ready", "Every configured model is listed; inference is not yet verified", identities
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
    attempt = current_attempt()
    result = asyncio.run(check_routing_stages(config.llm_routing,
        **({"should_stop": attempt.is_abandoned} if attempt else {})))
    return verification_row(result["stages"])


def _ml_assets(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.setup.assets import offline_asset_report

    report = offline_asset_report(settings)
    missing = [row["repo_id"] for row in report["models"] if not row["cached"]]
    attempts = report.get("network_attempts") or []
    ok = report["offline_ready"] and report["loadable"] and not attempts
    return _row(
        "ml_assets",
        "ready" if ok else "needs_action",
        "ML assets load in cache-only mode" if ok else (
            "ML assets attempted network access" if attempts else "ML assets are incomplete"
        ),
        ", ".join(attempts or missing) or f"{len(report['models'])} models loaded offline",
        action="Inspect offline model load" if attempts else "Prefetch ML assets",
        command=None if attempts else "uv run zotero-summarizer prefetch-models",
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
    row = _row(
        "rss_source",
        "ready" if count else "needs_action",
        f"{count} RSS source{'s' if count != 1 else ''} enabled"
        if count
        else "No RSS source is enabled",
        "Today needs at least one source.",
        action="Add a source in Settings",
    )
    row["recovery"]["action"] = "open_sources_settings"
    return row




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
    save_doctor(settings, payload)


def _invalidate_doctor_unlocked(settings: Settings) -> None:
    from uuid import uuid4

    write_json_atomic(settings.data_dir / "setup_credential_revision.json", {"revision": uuid4().hex})
    _save(settings, {"status": "not_started", "ready": False, "config_signature": ""})


def store_credential_and_invalidate(settings: Settings, name: str, key: str) -> dict[str, Any]:
    """A credential cannot change between Doctor's final check and Ready reply."""
    from zotero_summarizer.services.llm.credentials import store_api_key

    with _LOCK:
        _invalidate_doctor_unlocked(settings)
        try:
            return store_api_key(name, key)
        finally:
            state().invalidate_stage_clients()


def _config_signature(settings: Settings) -> str:
    """Invalidate old Doctor success after routing, goals, or paths change."""
    import hashlib
    import yaml
    from zotero_summarizer.services.llm.credentials import get_api_key

    digest = hashlib.sha256(OWNERSHIP_REVISION.encode())
    digest.update(policy_configuration_signature(settings).encode())
    if settings.config_path.is_file():
        try:
            config = read_config(settings.config_path, settings.calibration_path)
            for provider in config.llm_routing.providers:
                if not provider.is_local:
                    digest.update(repr((provider.name, get_api_key(provider.api_key_env))).encode())
        except (ValueError, yaml.YAMLError):
            digest.update(b"invalid-credential-configuration")
    for path in (settings.config_path, settings.calibration_path):
        digest.update(path.read_bytes() if path.is_file() else b"")
    saved = _saved_paths(settings)
    credential_revision = read_json_or_empty(settings.data_dir / "setup_credential_revision.json")
    digest.update(str((saved.get("ZOTERO_DATA_DIR"), saved.get("PDF_ROOT"),
                       settings.zotero_data_dir, settings.pdf_root, offline_requested(),
                       credential_revision.get("revision"))).encode())
    if settings.triage_db_path.is_file():
        with closing(sqlite3.connect(
            f"file:{settings.triage_db_path}?mode=ro", uri=True, timeout=10
        )) as conn:
            try:
                feeds = conn.execute(
                    "SELECT id, name, url FROM rss_feeds WHERE enabled = 1 ORDER BY id"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table: rss_feeds" not in str(exc):
                    raise
                feeds = []  # first run, before bootstrap creates feed tables
        digest.update(repr(feeds).encode())
    else:
        digest.update(b"feed-db-not-created")
    return digest.hexdigest()


def doctor_status(settings: Settings) -> dict[str, Any]:
    path = settings.data_dir / "setup_doctor.json"
    payload = read_json_or_empty(path)
    active = _ACTIVE if _ACTIVE and _ACTIVE.run_id == payload.get("run_id") else None
    restart = _restart_required(settings)
    if (restart or not payload or payload.get("verification_revision") != ADMISSION_REVISION
            or payload.get("config_signature") != _config_signature(settings)):
        checks = [_row(check_id, "not_started", "Not checked yet") for check_id in _CHECKS]
        if restart:
            checks[0] = _row("environment", "needs_action", "Restart app to apply new Zotero/PDF paths",
                             action="Retry after restart")
        result = {
            "verification_revision": ADMISSION_REVISION,
            "status": "needs_action" if restart else "not_started",
            "ready": False,
            "checks": checks,
            "modes": {},
            "last_successful_at": None,
            "config_signature": _config_signature(settings),
            **({"status": "running", "run_id": active.run_id, "attempt": active.receipt()} if active else {}),
        }
        result["request_attempts"] = request_attempts(settings)
        withhold_local_ready(settings, result)
        result["modes"] = _modes(settings, result["checks"])
        return result
    if active:
        payload["attempt"] = active.receipt()
    elif payload.get("status") == "running":
        payload.update(status="needs_action", ready=False,
                       attempt={"run_id": payload.get("run_id"), "ownership": "unresolved",
                                "callback_state": "unknown", "finished": False, "hold": True})
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
                payload["status"] = "needs_action"
    if payload.get("status") != "ready" or any(
        row["id"] in _REQUIRED and row["status"] != "ready" for row in payload["checks"]
    ):
        payload["ready"] = False
    withhold_local_ready(settings, payload)
    payload["modes"] = _modes(settings, payload["checks"])
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
    statuses = {row["id"]: row["status"] for row in rows}
    assets = statuses["ml_assets"] == "ready"
    local_ready = local and all(statuses[key] == "ready" for key in ("llm_inference", "dry_run"))
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
    except Exception:  # noqa: BLE001 — withhold diagnostics rather than risk leaking a secret
        rows[:] = [_row(row["id"], "needs_action", "Credential redaction failed; review AI settings") for row in rows]
        return
    rows[:] = redact_diagnostics(rows, secrets)


def run_doctor(
    settings: Settings,
    *,
    check_ids: list[str] | None = None,
    fix: bool = False,
    attempt: SetupAttempt | None = None,
) -> dict[str, Any]:
    global _ACTIVE
    attempt = attempt or SetupAttempt()
    selected = list(dict.fromkeys(check_ids or _CHECKS))
    unknown = sorted(set(selected) - set(_CHECKS))
    if unknown:
        raise APIError(
            "unknown_doctor_check", f"Unknown check IDs: {unknown}", status_code=422
        )
    if _restart_required(settings):
        return doctor_status(settings)
    if not _LOCK.acquire(blocking=False):
        raise APIError(
            "doctor_running", "Setup checks are already running", status_code=409
        )
    try:
        _ACTIVE = attempt
        if fix:
            from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0

            bootstrap_phase0(settings)
        current = doctor_status(settings)
        started_signature = _config_signature(settings)
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
            "ready": False,
            "checks": [by_id[key] for key in _CHECKS],
            "started_at": _now(),
            "run_id": attempt.run_id,
            "attempt": attempt.receipt(),
            "config_signature": started_signature,
        }
        _save(settings, running)
        for check_id, result in checked_rows(settings, selected, _RUNNERS, attempt):
            by_id[check_id] = result
            rows = [by_id[key] for key in _CHECKS]
            _redact_details(settings, rows)
            _save(settings, {**running, "checks": rows, "attempt": attempt.receipt()})
        rows = [by_id[key] for key in _CHECKS]
        changed = attempt.is_abandoned() or _config_signature(settings) != started_signature
        if changed:
            rows[0] = _row("environment", "needs_action", "Setup changed or verification was abandoned",
                           action="Run checks again")
        _redact_details(settings, rows)
        ready = not changed and all(row["status"] == "ready" for row in rows if row["id"] in _REQUIRED)
        payload = {
            "status": "ready" if ready else "needs_action",
            "ready": ready,
            "checks": rows,
            "modes": _modes(settings, rows),
            "verification_revision": ADMISSION_REVISION,
            "attempt": attempt.receipt(),
            "finished_at": _now(),
            "last_successful_at": _now()
            if ready
            else current.get("last_successful_at"),
            "config_signature": started_signature,
        }
        return attempt.publish(payload, lambda value: _save(settings, value),
                               previous_success=current.get("last_successful_at"))
    finally:
        _ACTIVE = None
        _LOCK.release()
