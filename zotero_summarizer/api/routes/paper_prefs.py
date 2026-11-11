from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.settings import paper_auto_review_enabled

router = APIRouter()


async def get_paper_prefs() -> dict:
    """Frontend-visible paper-page switches, read from the environment per request.

    ``auto_review`` mirrors ``ZS_PAPER_AUTO_REVIEW`` (default OFF): the paper page
    only auto-starts a deep review on open when this is explicitly on; otherwise the
    owner presses *Generate*. Operational — not part of the review identity."""
    return {"auto_review": paper_auto_review_enabled()}


router.add_api_route("/api/paper/prefs", get_paper_prefs, methods=["GET"])
