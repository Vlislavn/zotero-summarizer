from __future__ import annotations

import asyncio

from fastapi import APIRouter

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.llm import model_list, operational_check

router = APIRouter()
# Manual "is it operational" probe: checks each pipeline stage's configured
# provider and returns per-stage operational|fail. The app starts regardless of
# provider availability; this is how the user verifies stages on demand.
router.add_api_route("/api/admin/llm-check", operational_check.check_stages, methods=["POST"])


async def list_provider_models(provider: ProviderConfig) -> dict:
    """List the models a provider serves, for the Settings model-picker.

    Body is one provider profile (from the in-progress edit, not the saved
    config) so the user can pick a model before saving. The listing makes a
    blocking network call, so it runs in a worker thread to keep the event loop
    free — the same reason the operational check offloads its probes.
    """
    models = await asyncio.to_thread(model_list.list_models_for_provider, provider)
    return {"provider": provider.name, "type": provider.type.value, "models": models}


# Model discovery for the Settings picker: POST a provider profile, get its
# available model ids (OpenAI-compatible /models or the Anthropic models list).
router.add_api_route("/api/admin/llm-models", list_provider_models, methods=["POST"])
