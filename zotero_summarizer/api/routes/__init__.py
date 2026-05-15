from __future__ import annotations

from fastapi import FastAPI

from zotero_summarizer.api.routes import (
    config, corpus, daily, golden, health, pages, pending, relabel_audit,
    results, review, summaries, triage, zotero,
)


def include_routes(app: FastAPI) -> None:
    for module in (
        pages, health, summaries, corpus, results, zotero, triage, pending,
        review, relabel_audit, daily, golden, config,
    ):
        app.include_router(module.router)
