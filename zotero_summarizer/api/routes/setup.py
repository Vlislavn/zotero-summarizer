"""First-run setup endpoints (``/api/setup/*``).

Thin handlers over ``services/setup``: a readiness probe, a read-only Zotero-dir
detector, an allowlisted ``.env`` path writer, and a dry-run config validator.
The same service functions back the ``zotero-summarizer setup`` CLI.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from zotero_summarizer.models.setup import (
    DetectZoteroResponse,
    SetupStatusResponse,
    UpdatePathsRequest,
    UpdatePathsResponse,
    ValidateConfigRequest,
    ValidateConfigResponse,
)
from zotero_summarizer.services.setup import (
    detect_zotero_data_dirs,
    get_setup_status,
    validate_config_draft,
    write_env_paths,
)
from zotero_summarizer.services._common import settings as get_settings

router = APIRouter()


async def setup_status() -> SetupStatusResponse:
    """Aggregate readiness across config / LLM / paths / Zotero / classifier."""
    return await get_setup_status()


async def detect_zotero() -> DetectZoteroResponse:
    """List candidate Zotero data dirs (read-only probe), db_exists first."""
    candidates = await asyncio.to_thread(detect_zotero_data_dirs)
    return DetectZoteroResponse(candidates=candidates)


async def update_paths(req: UpdatePathsRequest) -> UpdatePathsResponse:
    """Persist the allowlisted path keys into ``.env`` (restart required).

    Only the keys the caller actually supplied (non-null) are written — an
    omitted field leaves its current ``.env`` line untouched. The allowlist +
    path-existence checks (→ 422) live in the service.
    """
    updates: dict[str, str] = {}
    if req.pdf_root is not None:
        updates["PDF_ROOT"] = req.pdf_root
    if req.zotero_data_dir is not None:
        updates["ZOTERO_DATA_DIR"] = req.zotero_data_dir
    env_path = get_settings().env_path
    return await asyncio.to_thread(write_env_paths, env_path, updates)


async def validate_config(req: ValidateConfigRequest) -> ValidateConfigResponse:
    """Validate a GoalsConfig draft; optionally probe the default provider. Never
    persists or hot-swaps."""
    return await validate_config_draft(req)


router.add_api_route("/api/setup/status", setup_status, methods=["GET"])
router.add_api_route("/api/setup/detect-zotero", detect_zotero, methods=["GET"])
router.add_api_route("/api/setup/paths", update_paths, methods=["PUT"])
router.add_api_route("/api/setup/validate-config", validate_config, methods=["POST"])
