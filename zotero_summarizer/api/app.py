from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import include_routes
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services import lifecycle
from zotero_summarizer.settings import Settings


# Phase 1.18 Step 1: React annotation-verdict app, built by `cd frontend && npm run build`.
# Vite emits assets with base "/annotate/" so they resolve when mounted here.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


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

    # Mount the React annotation tool when its build is present. The guard
    # is a deliberate boundary check, not error masking: in CI / test
    # environments the React build may legitimately not exist and the
    # backend tests must still pass. To produce the build, run
    # `cd frontend && npm run build`. Once mounted, /annotate serves the SPA.
    if _FRONTEND_DIST.exists() and (_FRONTEND_DIST / "index.html").exists():
        app.mount(
            "/annotate",
            StaticFiles(directory=str(_FRONTEND_DIST), html=True),
            name="annotate",
        )
    return app


app = create_app()
