from __future__ import annotations

from fastapi import APIRouter
from pydantic import ValidationError

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import GoalsConfig

from zotero_summarizer.services import config

router = APIRouter()
router.add_api_route("/api/config", config.get_runtime_config, methods=["GET"])


async def replace_config(payload: dict):
    revision = payload.get('_revision')
    if not isinstance(revision, str) or not revision:
        raise APIError('config_revision_required', 'Refresh configuration before saving this full-config draft.', status_code=428)
    try:
        candidate = GoalsConfig.model_validate(payload)
    except ValidationError:
        raise APIError('invalid_config', 'Configuration validation failed', status_code=422) from None
    return await config.update_runtime_config(candidate, expected_revision=revision)


router.add_api_route('/api/config', replace_config, methods=['PUT'])
