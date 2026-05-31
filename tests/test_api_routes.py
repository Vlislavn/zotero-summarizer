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
