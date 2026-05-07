from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import HealthResponse
from zotero_summarizer.services import health

router = APIRouter()
router.add_api_route("/api/health", health.health, methods=["GET"], response_model=HealthResponse)
