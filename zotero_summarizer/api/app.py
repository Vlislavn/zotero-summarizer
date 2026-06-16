from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import include_routes
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services import lifecycle
from zotero_summarizer.settings import Settings


# Phase 1.18 Step 2: the React SPA owns the root URL. The legacy Alpine
# UI was deleted at the end of the parity-check window; ``frontend/`` is
# the only UI from this point on.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _install_spa(app: FastAPI) -> None:
    """Serve the React SPA at /.

    Three pieces:
      * ``/assets/...`` and other build artefacts -> static files.
      * Direct deep-links like ``/today`` -> ``index.html`` (SPA routing).
      * ``/`` -> ``index.html``.
    The order matters: API routes are already registered, so they take
    priority over the SPA's catch-all.
    """
    if not (_FRONTEND_DIST.exists() and (_FRONTEND_DIST / "index.html").exists()):
        return

    index_html = _FRONTEND_DIST / "index.html"

    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="spa-assets",
    )

    async def spa_index() -> FileResponse:
        return FileResponse(str(index_html))

    # The catch-all uses a path param so /today, /annotate, /settings, etc.
    # all resolve back to index.html. The leading "/" handler is separate
    # so an empty path still works.
    app.add_api_route("/", spa_index, methods=["GET"], include_in_schema=False)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(request: Request, full_path: str) -> FileResponse:
        # Anything under /api/ or /assets/ must NOT be SPA-shadowed;
        # FastAPI's router prioritises explicit routes, so this only
        # fires for unknown paths. The check is a belt-and-suspenders
        # guard against future route shapes accidentally falling through.
        if full_path.startswith(("api/", "assets/")):
            raise FileNotFoundError(f"unknown path: {full_path}")
        return FileResponse(str(index_html))


def create_app(settings: Settings | None = None) -> FastAPI:
    effective_settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        set_context(AppContext(settings=effective_settings))
        # Phase 0: make a fresh checkout runnable with no manual `cp`/`migrate`.
        # Idempotent + safe (never overwrites existing files); must run BEFORE
        # startup, which fails fast on a missing goals.yaml.
        from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0

        bootstrap_phase0(effective_settings)
        lifecycle.startup()
        yield

    app = FastAPI(
        title="Zotero Article Summarization Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    include_routes(app)
    _install_spa(app)
    return app
