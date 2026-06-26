from __future__ import annotations

from fastapi import FastAPI

from zotero_summarizer.api.routes import (
    admin, config, corpus, daily, golden, health, library, llm, pending,
    relabel_audit, results, review, setup, triage, zotero,
)


def include_routes(app: FastAPI) -> None:
    for module in (
        health, corpus, results, zotero, triage, pending,
        review, relabel_audit, daily, golden, admin, config, library, llm, setup,
    ):
        app.include_router(module.router)
