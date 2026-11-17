"""Method card ("How it works"): read the state, or start the one background build per paper."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from zotero_summarizer.models.paper_keys import validate_paper_item_key
from zotero_summarizer.services.library import method_card

router = APIRouter()


async def get_method_card(item_key: str) -> dict[str, Any]:
    """State + card when built; never starts a model call."""
    return await asyncio.to_thread(method_card.request, validate_paper_item_key(item_key), start=False)


async def build_method_card(item_key: str) -> dict[str, Any]:
    """Idempotent start (one job per paper, failures retried no sooner than 300 s); poll the GET."""
    return await asyncio.to_thread(method_card.request, validate_paper_item_key(item_key), start=True)


router.add_api_route("/api/library/method-card/{item_key}", get_method_card, methods=["GET"])
router.add_api_route("/api/library/method-card/{item_key}", build_method_card, methods=["POST"])
