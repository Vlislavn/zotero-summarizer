"""Setup Doctor checks for the host, config, Zotero, and database."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any

from zotero_summarizer.models.providers import STAGES, resolve_stage
from zotero_summarizer.services._common import read_config
from zotero_summarizer.settings import Settings


def _row(
    check_id: str,
    status: str,
    message: str,
    detail: str = "",
    *,
    action: str = "Retry",
    command: str | None = None,
) -> dict[str, Any]:
    recovery = {"label": action}
    if command:
        recovery["command"] = command
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "detail": detail,
        "recovery": recovery,
    }


def _environment(settings: Settings) -> dict[str, Any]:
    memory = __import__("psutil").virtual_memory().total / 1024**3
    disk_root = (
        settings.data_dir if settings.data_dir.exists() else settings.data_dir.parent
    )
    disk = shutil.disk_usage(disk_root).free / 1024**3
    paths = (settings.project_root, settings.data_dir)
    ok = sys.version_info >= (3, 10) and all(
        path.exists() and os.access(path, os.W_OK) for path in paths
    )
    detail = f"Python {sys.version.split()[0]}; {memory:.1f} GB memory; {disk:.1f} GB free disk"
    return _row(
        "environment",
        "ready" if ok else "needs_action",
        "App environment is writable" if ok else "App environment needs repair",
        detail,
        action="Run safe fixes",
        command="uv run zotero-summarizer doctor --fix",
    )


def _local_profile(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.setup.profiles import (
        LOCAL_PROFILES,
        local_profile_catalog,
    )

    config = read_config(settings.config_path, settings.calibration_path)
    if not config.llm_enabled:
        return _row(
            "local_profile",
            "unavailable",
            "AI features are disabled",
            "ML-only triage does not need a local inference profile.",
        )
    resolved = [resolve_stage(config.llm_routing, stage) for stage in STAGES]
    if not all(item.provider.is_local for item in resolved):
        return _row(
            "local_profile",
            "unavailable",
            "Hosted or hybrid inference is active",
            "Local hardware profiles do not apply.",
            action="Change profile",
        )
    model = resolved[0].model
    profile_id = next(
        (name for name, row in LOCAL_PROFILES.items() if row["model"] == model),
        "existing",
    )
    profile = next(
        row
        for row in local_profile_catalog(settings)["profiles"]
        if row["id"] == profile_id
    )
    return _row(
        "local_profile",
        "ready" if profile["compatible"] else "needs_action",
        f"{profile['label']} profile is active",
        profile["compatibility_detail"],
        action="Choose a safe profile",
    )


def _research_goals(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.services.setup.validate import has_personal_goals

    config = read_config(settings.config_path, settings.calibration_path)
    ok = has_personal_goals(config)
    return _row(
        "research_goals",
        "ready" if ok else "needs_action",
        "Research goals are personalized"
        if ok
        else "Replace the example research goals",
        action="Edit research goals",
    )


def _zotero(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.integrations.zotero_read import ZoteroReader

    stats = ZoteroReader(settings.zotero_data_dir).get_library_stats()
    return _row(
        "zotero",
        "ready",
        f"Zotero metadata is readable ({stats['total_items']} items)",
        str(settings.zotero_data_dir),
        action="Choose Zotero folder",
    )


def _schema_version(path: Path, namespace: str) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT version FROM schema_migrations WHERE namespace = ?",
            (namespace,),
        ).fetchone()
    return int(row[0]) if row else 0


def _database(settings: Settings) -> dict[str, Any]:
    from zotero_summarizer.storage.migrations import SCHEMA_VERSION

    versions = {
        "triage": _schema_version(settings.triage_db_path, "triage"),
        "corpus": _schema_version(settings.corpus_db_path, "corpus"),
    }
    ok = all(version == SCHEMA_VERSION for version in versions.values())
    return _row(
        "database",
        "ready" if ok else "needs_action",
        "Database migrations are current" if ok else "Database migration is required",
        f"installed={versions}; target={SCHEMA_VERSION}",
        action="Run migrations",
        command="uv run zotero-summarizer doctor --fix",
    )


__all__ = [
    "_row",
    "_environment",
    "_local_profile",
    "_research_goals",
    "_zotero",
    "_database",
]
