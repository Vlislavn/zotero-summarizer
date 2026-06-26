"""Aggregate the first-run readiness of the app (``GET /api/setup/status``).

One call answers "is this install configured enough to use?" across four axes:
config validity, the default LLM provider (reachable + key present), the
filesystem paths, and the Zotero library. ``ready`` is the hard gate
(config.valid AND research_goals AND llm.api_key_present AND zotero.db_found);
reachability + the trained classifier are advisory.

SECURITY: this NEVER returns an API-key value. It reads only the *presence* of
the env var named by ``api_key_env`` (``bool(os.getenv(name))``) and reports a
BOOL — the env-var NAME is surfaced, the value never is.
"""
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
from zotero_summarizer.services.llm import operational_check
from zotero_summarizer.services.model.model_card import model_card
from zotero_summarizer.services.zotero.zotero import zotero_status_payload


def _config_status() -> tuple[ConfigStatus, object | None]:
    """Read goals.yaml → (status, parsed_config|None). The parsed config (when
    valid) is reused for the LLM section so we don't read+validate twice."""
    config_path = settings().config_path
    if not config_path.exists():
        return ConfigStatus(present=False, valid=False, research_goals_count=0, error=None), None
    try:
        config = read_config(config_path)
    except (pydantic.ValidationError, ValueError, yaml.YAMLError) as exc:
        # I/O boundary: a present-but-broken config file (bad YAML or a schema
        # violation) is a real, reportable state — not a crash. The setup UI shows
        # the error so the user can fix it.
        return ConfigStatus(present=True, valid=False, research_goals_count=0, error=str(exc)), None
    goals = [g for g in (config.research_goals or []) if str(g).strip()]
    return ConfigStatus(present=True, valid=True, research_goals_count=len(goals), error=None), config


async def _llm_status(config: object | None) -> LlmStatus:
    """Default-stage provider snapshot: provider/model names, key presence (BOOL
    only, never the value), and advisory reachability. When the config is invalid
    there is no routing to inspect, so everything is null/false."""
    if config is None:
        return LlmStatus(
            default_provider=None, default_model=None, api_key_env=None,
            api_key_present=False, reachable=False, detail="config invalid or missing",
        )
    resolved = resolve_stage(config.llm_routing, "deep_review")  # type: ignore[attr-defined]
    provider, model = resolved.provider, resolved.model
    # Presence check ONLY — read the named env var and coerce to a bool. The value
    # is never returned, logged, or stored.
    api_key_present = bool(os.getenv(provider.api_key_env, "").strip())

    reachability = await operational_check.check_reachability()
    default_row = next(
        (row for row in reachability.get("stages", []) if row.get("stage") == "deep_review"),
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
    """Zotero readiness from the live reader + status payload. ``db_found`` is the
    gating signal; library/feed counts are best-effort from the live reader."""
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
    """Assemble the full setup-readiness snapshot.

    ``ready`` = config valid AND ≥1 research goal AND the default provider's key
    is present AND the Zotero DB was found. Reachability + classifier are advisory
    and deliberately excluded from the gate (an install is "ready" before the LLM
    endpoint is up or the gate is trained)."""
    config_status, config = _config_status()
    llm = await _llm_status(config)
    paths = _path_status()
    zotero = _zotero_status()
    classifier = await _classifier_status()

    ready = bool(
        config_status.valid
        and config_status.research_goals_count > 0
        and llm.api_key_present
        and zotero.db_found
    )

    return SetupStatusResponse(
        ready=ready,
        config=config_status,
        llm=llm,
        paths=paths,
        zotero=zotero,
        classifier=classifier,
        subsystems=readiness.all_statuses(),
    )
