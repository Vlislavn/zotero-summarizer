"""API layer for zotero-summarizer.

This package is intentionally lazy. Eagerly importing `app` and `create_app`
from `api.app` triggers a circular import chain via `api.routes.pending` ->
`services.pending` -> `api.errors` -> back to `api.__init__` mid-load.
Consumers should import directly: `from zotero_summarizer.api.app import app`.
"""
from __future__ import annotations

__all__: list[str] = []
