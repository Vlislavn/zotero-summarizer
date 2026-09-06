"""Thin HTTP front-end over the shared setup services."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.models.setup import (
    DetectZoteroResponse,
    SetupStatusResponse,
    UpdatePathsRequest,
    UpdatePathsResponse,
    ValidateConfigRequest,
    ValidateConfigResponse,
)
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.setup import (
    detect_zotero_data_dirs,
    get_setup_status,
    validate_config_draft,
    write_env_paths,
)

router = APIRouter()


async def setup_status() -> SetupStatusResponse:
    """Aggregate readiness across config / LLM / paths / Zotero / classifier."""
    return await get_setup_status()


async def detect_zotero() -> DetectZoteroResponse:
    """List candidate Zotero data dirs (read-only probe), db_exists first."""
    candidates = await asyncio.to_thread(detect_zotero_data_dirs)
    return DetectZoteroResponse(candidates=candidates)


async def update_paths(req: UpdatePathsRequest) -> UpdatePathsResponse:
    """Persist supplied allowlisted paths; the service validates them."""
    updates: dict[str, str] = {}
    if req.pdf_root is not None:
        updates["PDF_ROOT"] = req.pdf_root
    if req.zotero_data_dir is not None:
        updates["ZOTERO_DATA_DIR"] = req.zotero_data_dir
    env_path = get_settings().env_path
    return await asyncio.to_thread(write_env_paths, env_path, updates)


async def validate_config(req: ValidateConfigRequest) -> ValidateConfigResponse:
    """Validate a draft and optionally probe it; write nothing."""
    return await validate_config_draft(req)


class CredentialRequest(BaseModel):
    name: str
    api_key: str


async def list_ai_presets() -> dict:
    """Return provider cards, compiled configs, and redacted credential status."""
    from zotero_summarizer.services.llm.presets import list_presets
    from zotero_summarizer.services.setup.profiles import local_profile_catalog

    presets, local = await asyncio.gather(
        asyncio.to_thread(list_presets),
        asyncio.to_thread(local_profile_catalog, get_settings()),
    )
    return {"presets": presets, "local_profiles": local}


async def save_ai_credential(req: CredentialRequest) -> dict:
    """Store one API key in the OS keyring; never echo it in the response."""
    from zotero_summarizer.services.llm.credentials import store_api_key

    return {"credential": await asyncio.to_thread(store_api_key, req.name, req.api_key)}


class CalibrateRequest(BaseModel):
    item_keys: list[str] | None = None
    papers: int = Field(default=3, ge=1, le=10)


async def calibrate(req: CalibrateRequest) -> dict:
    """Start the existing environment/text-budget calibration job."""
    from zotero_summarizer.services.setup import calibration_job

    return calibration_job.start(
        get_settings(), item_keys=req.item_keys, papers_limit=req.papers
    )


async def calibrate_status() -> dict:
    """Return live calibration progress."""
    from zotero_summarizer.services.setup import calibration_job

    return calibration_job.status()


class DoctorRequest(BaseModel):
    check_ids: list[str] | None = None
    fix: bool = False


async def doctor_status() -> dict:
    from zotero_summarizer.services.setup.doctor import doctor_status as read_status

    return await asyncio.to_thread(read_status, get_settings())


async def run_doctor(req: DoctorRequest) -> dict:
    from zotero_summarizer.services.setup.doctor import run_doctor as run_checks

    return await asyncio.to_thread(
        run_checks, get_settings(), check_ids=req.check_ids, fix=req.fix,
    )


router.add_api_route("/api/setup/doctor", doctor_status, methods=["GET"])
router.add_api_route("/api/setup/doctor", run_doctor, methods=["POST"])
router.add_api_route("/api/setup/status", setup_status, methods=["GET"])
router.add_api_route("/api/setup/detect-zotero", detect_zotero, methods=["GET"])
router.add_api_route("/api/setup/paths", update_paths, methods=["PUT"])
router.add_api_route("/api/setup/validate-config", validate_config, methods=["POST"])
router.add_api_route("/api/setup/ai-presets", list_ai_presets, methods=["GET"])
router.add_api_route("/api/setup/ai-credential", save_ai_credential, methods=["PUT"])
router.add_api_route("/api/setup/calibrate", calibrate, methods=["POST"])
router.add_api_route("/api/setup/calibrate/status", calibrate_status, methods=["GET"])
