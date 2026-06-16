from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_summarizer.api.app import create_app
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import library as library_routes
from zotero_summarizer.services.zotero import zotero as zotero_svc


class _PdfReader:
    def __init__(self, details):
        self._d = details

    def get_item_detail(self, key):
        return self._d.get(key)


def _use_reader(monkeypatch, reader):
    monkeypatch.setattr(zotero_svc, "state", lambda: SimpleNamespace(zotero_reader=reader))


def test_library_pdf_route_registered():
    paths = {getattr(route, "path", "") for route in create_app().routes}
    assert "/api/library/pdf/{item_key}" in paths
    assert "/api/library/render/{item_key}/build" in paths
    assert "/api/library/render/{item_key}/presentation" in paths


def test_get_item_pdf_serves_stored_file(monkeypatch, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 hello")
    _use_reader(monkeypatch, _PdfReader({"K1": {"pdf_path": str(pdf), "has_pdf": True, "title": "T"}}))
    resp = asyncio.run(library_routes.get_item_pdf("K1"))
    assert Path(resp.path) == pdf
    assert resp.media_type == "application/pdf"


def test_get_item_pdf_404_when_no_local_pdf(monkeypatch):
    _use_reader(monkeypatch, _PdfReader({"K1": {"pdf_path": "", "has_pdf": False, "title": "T"}}))
    with pytest.raises(APIError) as excinfo:
        asyncio.run(library_routes.get_item_pdf("K1"))
    assert excinfo.value.status_code == 404


def test_get_item_pdf_404_when_item_missing(monkeypatch):
    _use_reader(monkeypatch, _PdfReader({}))
    with pytest.raises(APIError) as excinfo:
        asyncio.run(library_routes.get_item_pdf("NOPE"))
    assert excinfo.value.status_code == 404


# --- paper brief + ask routes ---------------------------------------------------

def test_get_paper_presentation_serves_inline(monkeypatch, tmp_path):
    # Disposition must be inline so the reader pane can embed it in an <iframe>;
    # a filename alone would force a download instead of rendering.
    html = tmp_path / "brief.html"
    html.write_text("<html><body>brief</body></html>")
    monkeypatch.setattr(library_routes.paper_render, "presentation_path", lambda item_key: html)
    resp = asyncio.run(library_routes.get_paper_presentation("K1"))
    assert Path(resp.path) == html and resp.media_type == "text/html"
    disposition = resp.headers.get("content-disposition", "")
    assert "inline" in disposition and "attachment" not in disposition


def test_get_paper_figure_route_rejects_traversal_name():
    # The filename guard fires before any state lookup (422, no artifact needed).
    with pytest.raises(APIError) as excinfo:
        asyncio.run(library_routes.get_paper_figure("K1", "../secret.png"))
    assert excinfo.value.status_code == 422


def test_build_paper_render_route_passes_flags(monkeypatch):
    seen: dict = {}

    def _start(item_key, *, force, allow_arxiv_source):
        seen.update(item_key=item_key, force=force, allow=allow_arxiv_source)
        return {"status": "running", "item_key": item_key}

    monkeypatch.setattr(library_routes.paper_render, "start_build", _start)
    out = asyncio.run(
        library_routes.build_paper_render(
            "K1", library_routes.PaperRenderBuildRequest(force=True, allow_arxiv_source=True)
        )
    )
    assert out["status"] == "running"
    assert seen == {"item_key": "K1", "force": True, "allow": True}


def test_ask_paper_route_returns_grounded_and_abstained(monkeypatch):
    seen: dict = {}

    def _ask(item_key, question, *, mode):
        seen["mode"] = mode
        if "cohort" in question:
            return {"answer": None, "abstained": True, "quote": None, "mode": mode, "item_key": item_key}
        return {"answer": "ImageNet", "abstained": False, "quote": "a real sentence", "mode": mode}

    monkeypatch.setattr(library_routes.qa, "ask_paper", _ask)
    grounded = asyncio.run(
        library_routes.ask_paper(
            library_routes.AskPaperRequest(item_key="K1", question="dataset?", mode="full_text")
        )
    )
    assert grounded["answer"] == "ImageNet" and grounded["abstained"] is False
    assert seen["mode"] == "full_text"
    abstained = asyncio.run(
        library_routes.ask_paper(library_routes.AskPaperRequest(item_key="K1", question="cohort size?"))
    )
    assert abstained["abstained"] is True and abstained["answer"] is None


# --- reading-queue route: whole-library limit cap (the 422 the user hit) -------

def test_reading_queue_status_route_registered():
    paths = {getattr(route, "path", "") for route in create_app().routes}
    assert "/api/library/reading-queue/status" in paths


def test_get_reading_queue_rejects_over_cap():
    with pytest.raises(APIError) as excinfo:
        asyncio.run(library_routes.get_reading_queue(limit=10001))
    assert excinfo.value.status_code == 422


def test_get_reading_queue_rejects_below_one():
    with pytest.raises(APIError) as excinfo:
        asyncio.run(library_routes.get_reading_queue(limit=0))
    assert excinfo.value.status_code == 422


def test_get_reading_queue_accepts_whole_library_limit(monkeypatch):
    # 1, the frontend's 5000, and the exact 10000 cap must all be accepted and
    # passed through unchanged (guards a future re-tightening of the cap).
    seen: dict = {}

    def _stub(**kw):
        seen.update(kw)
        return {"items": [], "total_unread": 0, "status": "ready"}

    # A configured reader is a precondition of the happy path (the route now
    # fails fast with 503 when it's missing — see the regression test below).
    monkeypatch.setattr(library_routes, "get_zotero_reader_or_raise", lambda: object())
    monkeypatch.setattr(library_routes.reading_queue, "build_reading_queue", _stub)
    for limit in (1, 5000, 10000):
        out = asyncio.run(library_routes.get_reading_queue(limit=limit))
        assert out["status"] == "ready"
    assert seen["limit"] == 10000


def test_get_reading_queue_returns_503_when_zotero_unavailable(monkeypatch):
    # First run (no Zotero) must surface as a clean 503 at the boundary, never a
    # 500 from build_reading_queue's reader-unavailable RuntimeError. Regression
    # for the "Failed to load queue: Unexpected server error" the user would have
    # seen behind the first-run setup card.
    def _raise_unavailable():
        raise APIError(
            error="zotero_unavailable",
            message="Zotero database not found",
            status_code=503,
        )

    def _must_not_run(**_kw):
        raise AssertionError("build_reading_queue must not run when the reader is unavailable")

    monkeypatch.setattr(library_routes, "get_zotero_reader_or_raise", _raise_unavailable)
    monkeypatch.setattr(library_routes.reading_queue, "build_reading_queue", _must_not_run)

    with pytest.raises(APIError) as excinfo:
        asyncio.run(library_routes.get_reading_queue(limit=10))
    assert excinfo.value.status_code == 503
    assert excinfo.value.error == "zotero_unavailable"


def test_app_uses_canonical_routes_only(tmp_path, monkeypatch):
    # _install_spa only registers "/" when frontend/dist/index.html exists.
    # Create a stub so the assertion holds on a clean checkout (no built dist).
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "assets").mkdir()
    import zotero_summarizer.api.app as _app_mod
    monkeypatch.setattr(_app_mod, "_FRONTEND_DIST", dist)

    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}

    # Canonical API surface.
    assert "/api/health" in paths
    assert "/api/daily" in paths

    # Legacy aliases must stay deleted.
    assert "/health" not in paths
    assert "/summarize" not in paths
    assert "/batch_summarize" not in paths
    assert "/dashboard" not in paths
    assert "/api/summaries" not in paths

    # Phase 1.18 Step 2: the React SPA owns ``/``. The legacy Alpine
    # ``/results`` dashboard is gone. ``/`` is the SPA index; everything
    # else under SPA paths resolves through the catch-all to index.html.
    assert "/results" not in paths
    assert "/" in paths
