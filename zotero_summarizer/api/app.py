from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import include_routes
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services import lifecycle
from zotero_summarizer.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    effective_settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        set_context(AppContext(settings=effective_settings))
        lifecycle.startup()
        yield

    app = FastAPI(
        title="Zotero Article Summarization Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    include_routes(app)
    return app


app = create_app()
