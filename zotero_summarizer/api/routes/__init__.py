from __future__ import annotations

from fastapi import FastAPI

from zotero_summarizer.api.routes import config, corpus, health, pages, pending, results, summaries, triage, zotero


def include_routes(app: FastAPI) -> None:
    for module in (pages, health, summaries, corpus, results, zotero, triage, pending, config):
        app.include_router(module.router)
