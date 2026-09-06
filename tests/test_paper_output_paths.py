"""Persisted output paths are untrusted on HTTP serving and attachment enumeration."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import paper_figures, paper_render

_NAMES = ("brief_presentation.html", "paper.pdf", "fig1_result.png")
_ROUTES = ("presentation", "pdf", "figures/fig1_result.png", "attachments")


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    config = SimpleNamespace(
        paper_render_dir=tmp_path / "data" / "render",
        pdf_root=tmp_path / "library", pdf_cache_dir=tmp_path / "cache",
    )
    config.pdf_root.mkdir()
    config.pdf_cache_dir.mkdir()
    monkeypatch.setattr(paper_render, "settings", lambda: config)
    return config


def _publish(folder):
    paper_render._write_state("KEY", {
        "status": "completed", "pdf_path": str(folder / _NAMES[1]),
        "audit": {"status": "passed", "blocking": []}, "figures": [{"name": _NAMES[2]}],
        "outputs": {"presentation": str(folder / _NAMES[0]), "figures_dir": str(folder)},
    })


def _files(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in _NAMES:
        (folder / name).write_bytes(b"PAPER_CONTENT")


def _get(route):
    app = FastAPI()
    app.include_router(library.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        return client.get(f"/api/library/render/KEY/{route}")


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("kind", ["absolute", "prefix", "directory_link", "file_link", "parent"])
def test_external_outputs_cannot_be_served_or_attached(outputs, route, kind):
    outside = outputs.pdf_root.parent / ("library-other" if kind == "prefix" else "private")
    _files(outside)
    folder = outside
    if kind == "directory_link":
        folder = outputs.pdf_root / "linked"
        folder.symlink_to(outside, target_is_directory=True)
    elif kind == "file_link":
        folder = outputs.pdf_root / "paper"
        folder.mkdir()
        for name in _NAMES:
            (folder / name).symlink_to(outside / name)
    elif kind == "parent":
        folder = outputs.pdf_root / ".." / "private"
    _publish(folder)
    if route == "attachments":
        with pytest.raises(APIError) as caught:
            paper_figures.attachable_figures("KEY")
        assert caught.value.status_code == 403
    else:
        response = _get(route)
        assert response.status_code == 403
        assert response.json()["error"] == "path_not_allowed"
        assert b"PAPER_CONTENT" not in response.content


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("root_name", ["pdf_root", "pdf_cache_dir"])
def test_configured_roots_remain_usable(outputs, route, root_name):
    folder = getattr(outputs, root_name) / "paper"
    _files(folder)
    _publish(folder)
    if route == "attachments":
        assert paper_figures.attachable_figures("KEY") == [
            {"name": _NAMES[2], "path": str((folder / _NAMES[2]).resolve())},
        ]
    else:
        response = _get(route)
        assert response.status_code == 200
        assert response.content == b"PAPER_CONTENT"
        if route in ("pdf", "presentation"):
            assert response.headers["content-disposition"].startswith("inline;")


@pytest.mark.parametrize("route", ["figures/fig1_result.png", "attachments"])
def test_figure_link_cannot_leave_its_own_directory(outputs, route):
    folder = outputs.pdf_root / "figures"
    folder.mkdir()
    other = outputs.pdf_root / "other.png"
    other.write_bytes(b"OTHER_PAPER")
    (folder / _NAMES[2]).symlink_to(other)
    _publish(folder)
    if route == "attachments":
        with pytest.raises(APIError) as caught:
            paper_figures.attachable_figures("KEY")
        assert caught.value.status_code == 422
    else:
        assert _get(route).status_code == 422


def test_absent_figure_metadata_does_not_scan_working_directory(outputs, monkeypatch):
    (outputs.pdf_root / _NAMES[2]).write_bytes(b"UNRELATED")
    monkeypatch.chdir(outputs.pdf_root)
    paper_render._write_state("KEY", {
        "status": "completed", "audit": {"status": "passed", "blocking": []}, "outputs": {},
    })
    assert paper_figures.attachable_figures("KEY") == []
    assert _get("figures/fig1_result.png").status_code == 404


def test_figure_name_allowlist_rejects_trailing_newline(outputs):
    folder = outputs.pdf_root / "figures"
    _files(folder)
    (folder / ("fig2_other.png\n")).write_bytes(b"INVALID_NAME")
    _publish(folder)
    with pytest.raises(APIError) as caught:
        paper_render.figure_path("KEY", "fig2_other.png\n")
    assert caught.value.status_code == 422
    assert [f["name"] for f in paper_figures.attachable_figures("KEY")] == [_NAMES[2]]
