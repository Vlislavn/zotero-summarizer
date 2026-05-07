from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.services import config

router = APIRouter()
router.add_api_route("/api/config", config.get_runtime_config, methods=["GET"])
router.add_api_route("/api/config", config.update_runtime_config, methods=["PUT"])
