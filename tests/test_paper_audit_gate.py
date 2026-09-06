"""Only audited artifacts and their current figure manifest may be published/reused."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import _paper_read_html as html
from zotero_summarizer.services.library import paper_figures, paper_render


@pytest.fixture
def paper(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Audited Paper", fontsize=18)
        page.insert_text((72, 120), "Abstract", fontsize=14)
        page.insert_text((72, 150), "This paper evaluates a synthetic dataset.", fontsize=10)
        page.draw_rect(fitz.Rect(120, 300, 420, 430), color=(1, 0, 0), fill=(1, 0, 0))
        page.insert_text((72, 455), "Figure 1: Architecture diagram.", fontsize=9)
        doc.save(pdf)
    monkeypatch.setattr(paper_render, "settings", lambda: SimpleNamespace(
        paper_render_dir=tmp_path / "state", pdf_root=tmp_path, pdf_cache_dir=tmp_path / "cache",
    ))
    monkeypatch.setattr(paper_render, "_item_detail", lambda _: {"pdf_path": str(pdf), "title": "Audited Paper"})
    monkeypatch.setattr(paper_render, "_JOBS", {})
    return pdf


def _snapshot(directory):
    return {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def _break(monkeypatch, failure):
    if failure in {"empty", "latex"}:
        original = paper_render._paper_read_pdf.extract_pdf_content

        def broken(pdf, **kwargs):
            result = original(pdf, **kwargs)
            result.update(full_text="" if failure == "empty" else r"\section{bad} " * 12, sections=[])
            return result

        monkeypatch.setattr(paper_render._paper_read_pdf, "extract_pdf_content", broken)
    else:
        original = html._render_presentation

        def broken(*args, **kwargs):
            result = original(*args, **kwargs)
            if failure == "placeholder":
                return result.replace('id="ph-fig1"', 'id="lost-figure"')
            if failure == "map_value":
                return result.replace('"ph-fig1": "figures/', '"ph-fig1": "wrong/')
            return result.replace("const imageMap = {", "const lostMap = {")

        monkeypatch.setattr(html, "_render_presentation", broken)


@pytest.mark.parametrize("failure", ["empty", "latex", "placeholder", "map", "map_value"])
def test_blocking_audit_does_not_publish_files_or_completed_state(paper, monkeypatch, failure):
    _break(monkeypatch, failure)
    with pytest.raises(APIError) as caught:
        paper_render.build_paper_read("KEY")
    assert caught.value.error == "paper_audit_failed"
    assert caught.value.status_code == 422
    assert caught.value.details["audit"]["blocking"]
    assert paper_render._read_state("KEY") is None
    assert not list(paper.parent.rglob("*_presentation.html"))
    assert not list(paper.parent.rglob("*_audit.json"))
    assert not list(paper.parent.rglob("fig*.png"))


def test_failed_rebuild_preserves_previously_published_files(paper, monkeypatch):
    first = paper_render.build_paper_read("KEY")
    directory = Path(first["outputs"]["presentation"]).parent
    saved = _snapshot(directory)
    _break(monkeypatch, "empty")
    original = paper_render._paper_read_pdf.extract_pdf_figures

    def changed_figures(pdf, folder):
        figures = original(pdf, folder)
        (folder / figures[0]["name"]).write_bytes(b"REJECTED REBUILD IMAGE")
        return figures

    monkeypatch.setattr(paper_render._paper_read_pdf, "extract_pdf_figures", changed_figures)
    with pytest.raises(APIError, match="audit"):
        paper_render.build_paper_read("KEY", force=True)
    assert _snapshot(directory) == saved
    assert paper_render._read_state("KEY") == first


def test_output_write_error_before_publication_preserves_old_bundle(paper, monkeypatch):
    first = paper_render.build_paper_read("KEY")
    directory = Path(first["outputs"]["presentation"]).parent
    saved = _snapshot(directory)
    original = Path.write_text
    monkeypatch.setattr(paper_render.deep_review, "get_cached_review", lambda _: {
        "digest": {"tldr": "CHANGED BEFORE FAILED AUDIT WRITE"},
    })

    def fail_audit(path, *args, **kwargs):
        if path.name == "paper_audit.json":
            raise OSError("audit write failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_audit)
    with pytest.raises(OSError, match="audit write"):
        paper_render.build_paper_read("KEY", force=True)
    assert _snapshot(directory) == saved


@pytest.mark.parametrize("audit", [None, {}, "passed", {"status": "blocking", "blocking": ["empty"]},
                                  {"status": "passed", "blocking": ["invalid"]}])
def test_legacy_unapproved_completed_state_is_not_reused(paper, audit):
    first = paper_render.build_paper_read("KEY")
    paper_render._write_state("KEY", {**first, "title": "UNAPPROVED CACHE", "audit": audit})
    status = paper_render.render_paper("KEY")
    assert status["status"] == "error"
    assert status["error"] == "paper_audit_failed"
    result = paper_render.build_paper_read("KEY")
    assert result["title"] == "Audited Paper"
    assert result["audit"]["status"] == "passed"


@pytest.mark.parametrize("route", ["presentation", "pdf", "figure", "attachments"])
def test_failed_audit_cannot_be_served_or_attached(paper, route):
    first = paper_render.build_paper_read("KEY")
    paper_render._write_state("KEY", {**first, "audit": {"status": "blocking", "blocking": ["bad map"]}})
    if route == "attachments":
        with pytest.raises(APIError):
            paper_figures.attachable_figures("KEY")
        return
    if route == "figure":
        route = "figures/" + first["figures"][0]["name"]
    app = FastAPI()
    app.include_router(library.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        response = client.get(f"/api/library/render/KEY/{route}")
    assert response.status_code == 404
    assert response.json()["error"] == "not_ready"


def test_worker_reports_audit_details_and_never_completes(paper, monkeypatch):
    _break(monkeypatch, "empty")
    with pytest.raises(APIError, match="audit"):
        paper_render._build_job("KEY", force=True, allow_arxiv_source=False, allow_acquire_missing=False)
    state = paper_render.render_paper("KEY")
    assert state["status"] == "error"
    assert state["error"] == "paper_audit_failed"
    assert state["details"]["audit"]["blocking"]


def test_unlisted_leftover_figure_cannot_be_served_or_attached(paper):
    built = paper_render.build_paper_read("KEY")
    extra = Path(built["outputs"]["figures_dir"]) / "fig2_old.png"
    extra.write_bytes(b"OLD UNAPPROVED IMAGE")
    with pytest.raises(APIError) as caught:
        paper_render.figure_path("KEY", extra.name)
    assert caught.value.status_code == 404
    assert [fig["name"] for fig in paper_figures.attachable_figures("KEY")] == [built["figures"][0]["name"]]
    assert extra.read_bytes() == b"OLD UNAPPROVED IMAGE"


def test_figure_metadata_without_an_image_blocks_publication(paper, monkeypatch):
    monkeypatch.setattr(paper_render._paper_read_pdf, "extract_pdf_figures", lambda *_a: [
        {"name": "fig1_missing.png", "caption": "Figure 1: Missing image"},
    ])
    with pytest.raises(APIError) as caught:
        paper_render.build_paper_read("KEY")
    assert caught.value.error == "paper_audit_failed"
    assert any("image" in issue for issue in caught.value.details["audit"]["blocking"])


def test_minor_audit_warning_allows_publication_and_reuse(paper, monkeypatch):
    monkeypatch.setattr(paper_render, "_item_detail", lambda _: {
        "pdf_path": str(paper), "title": "Audited Paper", "authors": "Author 1",
    })
    built = paper_render.build_paper_read("KEY")
    assert built["audit"]["status"] == "passed"
    assert built["audit"]["blocking"] == []
    assert any("placeholder authors" in issue for issue in built["audit"]["minor"])
    assert paper_render.presentation_path("KEY").is_file()
    assert paper_render.build_paper_read("KEY") == built
