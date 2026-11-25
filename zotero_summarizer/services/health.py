from __future__ import annotations

from zotero_summarizer.models import GoalsConfig, HealthResponse
from zotero_summarizer.runtime import local_model_device_override
from zotero_summarizer.services._common import state


async def health() -> HealthResponse:
    app_state = state()
    local_model_device = local_model_device_override() or "auto"
    if app_state.app_state is None:
        return HealthResponse(
            status="starting", config_loaded=False, local_model_device=local_model_device,
        )

    config: GoalsConfig = app_state.app_state.config
    return HealthResponse(
        status="ok",
        config_loaded=True,
        draft_model=config.llm.draft_model,
        refine_model=config.llm.refine_model,
        api_base=config.llm.api_base,
        local_model_device=local_model_device,
    )
