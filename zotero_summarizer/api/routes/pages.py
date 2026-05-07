from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.services import pages

router = APIRouter()
router.add_api_route("/", pages.index_page, methods=["GET"])
router.add_api_route("/results", pages.dashboard_page, methods=["GET"])
