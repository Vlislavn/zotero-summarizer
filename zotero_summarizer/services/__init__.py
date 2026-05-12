"""Service layer for zotero-summarizer.

This package is intentionally a thin namespace. Eagerly re-exporting classes
from `pending`/`summarization`/`triage_jobs` here creates a circular import
because those modules pull in `api.errors`, which pulls in `api.app`, which
pulls back to `services.pending` mid-initialization. Consumers should import
directly from the submodule (e.g., `from zotero_summarizer.services.pending
import PendingChangePlanner`).
"""
from __future__ import annotations

__all__: list[str] = []
