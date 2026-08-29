"""Aggregate first-run readiness without returning credential values."""

from __future__ import annotations

import os
from pathlib import Path

import pydantic
import yaml

from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.models.setup import (
    ClassifierStatus,
    ConfigStatus,
    LlmStatus,
    PathEntry,
    PathStatus,
    SetupStatusResponse,
    ZoteroStatus,
)
from zotero_summarizer.services import readiness
from zotero_summarizer.services._common import read_config, settings, state
from zotero_summarizer.services.llm import credentials, operational_check
from zotero_summarizer.services.model.model_card import model_card
from zotero_summarizer.services.zotero.zotero import zotero_status_payload


def _config_status() -> tuple[ConfigStatus, object | None]:
    """Read goals.yaml once and return status plus the parsed config."""
    config_path = settings().config_path
    if not config_path.exists():
        return ConfigStatus(present=False, valid=False, research_goals_count=0), None
    try:
        config = read_config(config_path)
    except (pydantic.ValidationError, ValueError, yaml.YAMLError) as exc:
        return ConfigStatus(
            present=True, valid=False, research_goals_count=0, error=str(exc)
        ), None
    from zotero_summarizer.services.setup.validate import has_personal_goals

    goals = [g for g in (config.research_goals or []) if str(g).strip()]
    count = len(goals) if has_personal_goals(config) else 0
    return ConfigStatus(present=True, valid=True, research_goals_count=count), config


async def _llm_status(config: object | None) -> LlmStatus:
    """Return default-stage names, redacted key presence, and reachability."""
    if config is None:
        return LlmStatus(
            api_key_present=False, reachable=False, detail="config invalid or missing"
        )
    if not bool(getattr(config, "llm_enabled", True)):
        return LlmStatus(
            enabled=False,
            api_key_present=False,
            reachable=False,
            detail="AI features are disabled; ML-only triage remains available",
        )
    resolved = resolve_stage(config.llm_routing, "deep_review")  # type: ignore[attr-defined]
    provider, model = resolved.provider, resolved.model
    preset_local = provider.is_local and provider.api_key_env == "OLLAMA_API_KEY"
    api_key_present = (
        preset_local or credentials.get_api_key(provider.api_key_env) is not None
    )

    reachability = await operational_check.check_reachability()
    default_row = next(
        (
            row
            for row in reachability.get("stages", [])
            if row.get("stage") == "deep_review"
        ),
        {},
    )
    return LlmStatus(
        default_provider=provider.name,
        default_model=model,
        api_key_env=provider.api_key_env,
        api_key_present=api_key_present,
        reachable=bool(default_row.get("reachable", False)),
        detail=str(default_row.get("detail", "")),
    )


def _path_entry(value: str, env_var: str) -> PathEntry:
    return PathEntry(
        value=value,
        set=os.getenv(env_var) is not None,
        exists=Path(value).expanduser().exists(),
    )


def _path_status() -> PathStatus:
    current = settings()
    return PathStatus(
        pdf_root=_path_entry(str(current.pdf_root), "PDF_ROOT"),
        zotero_data_dir=_path_entry(str(current.zotero_data_dir), "ZOTERO_DATA_DIR"),
    )


def _zotero_status() -> ZoteroStatus:
    """Zotero readiness from the live reader + status payload. ``db_found`` is an
    advisory signal; library/feed counts are best-effort from the live reader."""
    payload = zotero_status_payload()
    db_found = bool(payload.available)
    stats = payload.stats or {}
    library_item_count = int(stats.get("total_items", 0) or 0)

    feed_count = 0
    reader = getattr(state(), "zotero_reader", None)
    if reader is not None:
        feed_count = len(reader.get_feed_groups())

    return ZoteroStatus(
        db_found=db_found,
        data_dir=str(payload.data_dir),
        db_path=str(payload.db_path),
        library_item_count=library_item_count,
        feed_count=feed_count,
        error=str(payload.error or ""),
    )


async def _classifier_status() -> ClassifierStatus:
    """Map the trained ModelCard to the advisory classifier panel. ``{"model":
    null}`` (no model on disk) → ``trained=false``."""
    card = await model_card()
    model = card.get("model")
    if not model:
        return ClassifierStatus(trained=False, classifier_name=None, trained_at=None)
    return ClassifierStatus(
        trained=True,
        classifier_name=model.get("classifier_name"),
        trained_at=model.get("trained_at"),
    )


async def get_setup_status() -> SetupStatusResponse:
    """Assemble readiness; Zotero and the classifier stay advisory."""
    config_status, config = _config_status()
    llm = await _llm_status(config)
    paths = _path_status()
    zotero = _zotero_status()
    classifier = await _classifier_status()

    from zotero_summarizer.services.setup.doctor import doctor_status

    verified = bool(doctor_status(settings()).get("ready"))
    configured = bool(
        config_status.valid
        and config_status.research_goals_count > 0
        and (not llm.enabled or llm.api_key_present)
    )
    ready = bool(configured and (not llm.enabled or llm.reachable) and verified)

    return SetupStatusResponse(
        configured=configured,
        ready=ready,
        config=config_status,
        llm=llm,
        paths=paths,
        zotero=zotero,
        classifier=classifier,
        subsystems=readiness.all_statuses(),
    )
