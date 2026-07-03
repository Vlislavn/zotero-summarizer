"""First-run setup endpoints (``/api/setup/*``).

Thin handlers over ``services/setup``: a readiness probe, a read-only Zotero-dir
detector, an allowlisted ``.env`` path writer, and a dry-run config validator.
The same service functions back the ``zotero-summarizer setup`` CLI.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

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


class CalibrateRequest(BaseModel):
    item_keys: list[str] | None = None
    papers: int = 3


async def calibrate(req: CalibrateRequest) -> dict:
    """"Calibrate to my setup" — Tier-1 env probe + Tier-2 text-budget sweep on the
    resolved deep-review endpoint, persisted to data/calibration.json (applied on next
    config load). Kicks the sweep off on a background thread and returns immediately with
    the initial job status; the client polls ``/api/setup/calibrate/status`` for live
    ``completed/total`` progress (the sweep is bounded but slow — a few papers × budgets).
    Remote endpoints are memory-safe; a local one is memory-pre-flighted."""
    from zotero_summarizer.services.setup import calibration_job

    return calibration_job.start(get_settings(), item_keys=req.item_keys, papers_limit=req.papers)


async def calibrate_status() -> dict:
    """Live calibration progress: ``{status, completed, total, phase, result, error}``.
    Mirrors the deep-review status poll so the card can show "reviewed N of M"."""
    from zotero_summarizer.services.setup import calibration_job

    return calibration_job.status()


async def list_profiles() -> dict:
    """The deployment presets + which one the current routing matches."""
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.setup.profiles import PROFILES, detect_profile

    cfg = await asyncio.to_thread(read_config, get_settings().config_path, get_settings().calibration_path)
    return {"profiles": PROFILES, "current": detect_profile(cfg)}


class SetProfileRequest(BaseModel):
    profile: str
    local_depth: str | None = None


async def set_deployment_profile(req: SetProfileRequest) -> dict:
    """Apply a preset (rewrites llm_routing: which stage runs local vs API + review depth)."""
    from zotero_summarizer.services.setup.profiles import set_profile

    return await asyncio.to_thread(set_profile, get_settings(), req.profile, local_depth=req.local_depth)


class MeasureProfileRequest(BaseModel):
    include_local: bool = False


async def measure_profile(req: MeasureProfileRequest) -> dict:
    """Measure which stages are token/compute-heavy per provider → recommend a profile.
    Remote-only by default (a local gen loads a multi-GB model); ``include_local`` opts in."""
    from zotero_summarizer.services.setup.profiles import run_full_profile_measure

    return await asyncio.to_thread(run_full_profile_measure, get_settings(), include_local=req.include_local)


router.add_api_route("/api/setup/profiles", list_profiles, methods=["GET"])
router.add_api_route("/api/setup/profile", set_deployment_profile, methods=["POST"])
router.add_api_route("/api/setup/profile/measure", measure_profile, methods=["POST"])
router.add_api_route("/api/setup/status", setup_status, methods=["GET"])
router.add_api_route("/api/setup/detect-zotero", detect_zotero, methods=["GET"])
router.add_api_route("/api/setup/paths", update_paths, methods=["PUT"])
router.add_api_route("/api/setup/validate-config", validate_config, methods=["POST"])
router.add_api_route("/api/setup/calibrate", calibrate, methods=["POST"])
router.add_api_route("/api/setup/calibrate/status", calibrate_status, methods=["GET"])
