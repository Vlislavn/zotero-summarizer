"""Pydantic models = the API/config contract. Split by group; see README.md."""
from __future__ import annotations

from zotero_summarizer.models.providers import *  # noqa: F401,F403
from zotero_summarizer.models.config import *  # noqa: F401,F403
from zotero_summarizer.models.triage import *  # noqa: F401,F403
from zotero_summarizer.models.api import *  # noqa: F401,F403
from zotero_summarizer.models.setup import *  # noqa: F401,F403

from zotero_summarizer.models import (
    api as _api,
    config as _config,
    providers as _providers,
    setup as _setup,
    triage as _triage,
)

__all__ = [*_providers.__all__, *_config.__all__, *_triage.__all__, *_api.__all__, *_setup.__all__]
