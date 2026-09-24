from __future__ import annotations

from fastapi import FastAPI

from zotero_summarizer.api.routes import (
    admin, config, corpus, daily, golden, health, library, llm, pending,
    rss,
    relabel_audit, results, review, search, setup, sync, triage, zotero,
)


def include_routes(app: FastAPI) -> None:
    for module in (
        health, corpus, results, zotero, triage, pending,
        review, relabel_audit, daily, golden, admin, config, library, llm, setup, rss, search, sync,
    ):
        app.include_router(module.router)
