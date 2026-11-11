"""Today AI-card endpoints (Todo #92).

- ``GET  /api/card/{key}``          — cached AI card or a facts-only card
  (``ai_available:false`` + reason). NEVER triggers synchronous model work
  beyond the on-demand facts the collector already computes.
- ``POST /api/card/{key}/generate`` — generate ONE paper's card, idempotent
  (a fresh cached card is returned as-is), honouring consent and every spend
  guard. A ``consent_token`` matching the current card boundary records durable
  consent; a stale token is rejected with ``processing_boundary_changed``.

``key`` is the paper's stable/feed key (``arxiv:…``/``doi:…``/``key:…``), resolved
through the facts collector's alias resolver.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from zotero_summarizer.services.card import card as card_service

router = APIRouter()


class GenerateRequest(BaseModel):
    consent_token: str | None = None


def get_card(key: str) -> dict:
    return card_service.read_card(key)


def generate_card(key: str, body: GenerateRequest | None = None) -> dict:
    # Sync handler: FastAPI runs blocking generation (PDF + LLM) in the threadpool,
    # so concurrent on-demand calls overlap instead of blocking the event loop.
    consent_token = body.consent_token if body is not None else None
    return card_service.generate_card(key, consent_token=consent_token)


router.add_api_route("/api/card/{key}", get_card, methods=["GET"])
router.add_api_route("/api/card/{key}/generate", generate_card, methods=["POST"])
