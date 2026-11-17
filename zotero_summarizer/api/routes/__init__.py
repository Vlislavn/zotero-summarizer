from __future__ import annotations

from fastapi import FastAPI

from zotero_summarizer.api.routes import (
    admin, config, corpus, daily, golden, health, library, llm, pending,
    research_feed, rss,
    relabel_audit, results, review, search, setup, sync, triage, zotero,
    _card, feedback_analysis, method_card, paper_prefs, review_feedback,
)


def include_routes(app: FastAPI) -> None:
    for module in (
        health, corpus, results, zotero, triage, pending,
        review, relabel_audit, daily, golden, admin, config, library, llm, setup, rss, search, sync,
        research_feed, _card, feedback_analysis, method_card, paper_prefs, review_feedback,
    ):
        app.include_router(module.router)
