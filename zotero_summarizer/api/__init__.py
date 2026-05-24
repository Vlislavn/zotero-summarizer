"""API layer for zotero-summarizer.

This package is intentionally lazy. Eagerly importing `create_app` from
`api.app` triggers a circular import chain via `api.routes.pending` ->
`services.pending` -> `api.errors` -> back to `api.__init__` mid-load.
Consumers import directly: `from zotero_summarizer.api.app import create_app`.
The app is built via that factory (uvicorn uses `api.app:create_app` with
`factory=True`), so importing the module has no side effects.
"""
from __future__ import annotations

__all__: list[str] = []
