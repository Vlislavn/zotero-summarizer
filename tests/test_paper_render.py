"""paper_render: paper-render-compatible artifact generation."""
from __future__ import annotations

import json
import types
from pathlib import Path

import fitz
import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _paper_read_html, paper_render


def _make_pdf(path, *, arxiv: bool = False, refs: bool = True):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 70), "GlassNet: A Synthetic Paper", fontsize=18)
    if arxiv:
        page.insert_text((72, 95), "arXiv:2401.00001v2", fontsize=9)
    page.insert_text((72, 125), "Abstract", fontsize=14)
    page.insert_text((72, 150), "We study image classification on ImageNet.", fontsize=10)
    page.insert_text((72, 205), "1 Introduction", fontsize=14)
    page.insert_text((72, 230), "The method improves training stability.", fontsize=10)
    page.draw_rect(fitz.Rect(120, 300, 420, 430), color=(0, 0, 0), width=2)
    page.draw_line((120, 430), (420, 300), color=(1, 0, 0), width=2)
    page.insert_text((72, 455), "Figure 1: Vector architecture diagram.", fontsize=9)
    if refs:
        page2 = doc.new_page()
        page2.insert_text((72, 80), "References", fontsize=14)
        page2.insert_text((72, 115), "[1] A. Author. First paper.", fontsize=10)
        page2.insert_text((72, 135), "[2] B. Author. Second paper.", fontsize=10)
    doc.save(str(path))
    doc.close()


def _make_tex_source(pdf):
    source = pdf.parent / "source"
    figs = source / "figs"
    figs.mkdir(parents=True)
    fig_pdf = figs / "arch.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    page.draw_rect(fitz.Rect(20, 20, 180, 100), color=(0, 0, 1), width=2)
    doc.save(str(fig_pdf))
    doc.close()
    (source / "main.tex").write_text(
        r"""
\documentclass{article}
\title{GlassNet: A Synthetic Paper}
\author{Ada Lovelace}
\begin{abstract}We study ImageNet classification.\end{abstract}
\keywords{ImageNet, GlassNet}
\begin{document}
\section{Introduction}
This is the motivation.
\section{Method}
The architecture is shown in \autoref{fig:arch}.
\begin{figure}
\includegraphics{figs/arch}
\caption{GlassNet architecture.}
\label{fig:arch}
\end{figure}
\section{Experiments}
Top-1 accuracy is 85.3 percent.
\end{document}
""",
        encoding="utf-8",
    )
    (source / "references.bib").write_text(
        "@article{a,title={A}}\n@inproceedings{b,title={B}}\n",
        encoding="utf-8",
    )
    return source


def test_build_paper_read_uses_local_tex_and_writes_expected_outputs(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    _make_tex_source(pdf)

    artifact = paper_render.build_paper_read_for_pdf(pdf, title="GlassNet")

    assert artifact["status"] == "completed"
    assert artifact["source_tier"] == "local_tex"
    assert artifact["references_count"] == 2
    assert artifact["figures_count"] == 1
    assert (tmp_path / "figures" / artifact["figures"][0]["name"]).is_file()
    notes = artifact["outputs"]["notes"]
    html = artifact["outputs"]["presentation"]
    assert notes.endswith("_notes.md") and html.endswith("_presentation.html")
    notes_text = open(notes, encoding="utf-8").read()
    assert "GlassNet" in notes_text
    assert "Quick Reference" in notes_text
    html_text = open(html, encoding="utf-8").read()
    assert "const imageMap" in html_text
    assert 'id="ph-fig1"' in html_text
    assert artifact["audit"]["status"] == "passed"


def test_pdf_fallback_renders_vector_figure_regions_not_raw_page(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)

    artifact = paper_render.build_paper_read_for_pdf(pdf)

    assert artifact["source_tier"] == "pdf"
    assert artifact["n_pages"] == 2
    assert artifact["references_count"] == 2
    assert artifact["figures_count"] == 1
    fig_path = tmp_path / "figures" / artifact["figures"][0]["name"]
    assert fig_path.is_file()
    with fitz.open(fig_path) as img:
        # Region crop should be much shorter than a full page render.
        assert img[0].rect.height < 1400


def test_arxiv_source_download_requires_explicit_consent(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf, arxiv=True)
    calls = {"n": 0}

    def _download(arxiv_id, pdf_path):
        calls["n"] += 1
        assert arxiv_id == "2401.00001v2"
        return None

    monkeypatch.setattr(paper_render._paper_read_tex, "download_arxiv_source", _download)
    no_consent = paper_render.build_paper_read_for_pdf(pdf, allow_arxiv_source=False)
    assert no_consent["source_tier"] == "pdf"
    assert calls["n"] == 0

    paper_render.build_paper_read_for_pdf(pdf, allow_arxiv_source=True)
    assert calls["n"] == 1


def test_audit_reports_missing_placeholder(tmp_path):
    html = tmp_path / "x.html"
    notes = tmp_path / "x.md"
    html.write_text("<html><script>const imageMap={}</script><section lang-zh>A</section><section lang-en>A</section></html>")
    notes.write_text("notes")
    audit = _paper_read_html._audit_presentation(
        html, notes, [{"name": "fig1_arch.png", "caption": "Figure 1"}]
    )
    assert audit["status"] == "blocking"
    assert any("ph-fig1" in issue for issue in audit["blocking"])


def test_presentation_has_standard_sections_and_no_bilingual_markup(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    _make_tex_source(pdf)
    artifact = paper_render.build_paper_read_for_pdf(pdf, title="GlassNet")
    html_text = open(artifact["outputs"]["presentation"], encoding="utf-8").read()
    # Decision-ordered layout: figures section + a footer that points to the PDF
    # (the full paper body is no longer embedded — it's a triage brief).
    assert 'id="figures"' in html_text
    assert "open the original PDF in Zotero" in html_text
    assert 'id="sections"' not in html_text  # no full-paper-body dump
    # No bilingual class or attribute markers — English-only presentation.
    assert 'class="zh"' not in html_text
    assert 'class="en"' not in html_text
    assert 'lang-zh' not in html_text
    assert 'lang-en' not in html_text


def test_tex_figures_distributed_across_sections_not_clustered_in_first(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    source = pdf.parent / "source"
    figs = source / "figs"
    figs.mkdir(parents=True)
    # Create two figures so we can verify they land in different sections.
    for name in ("arch.pdf", "result.pdf"):
        doc = fitz.open()
        page = doc.new_page(width=200, height=120)
        page.draw_rect(fitz.Rect(20, 20, 180, 100), color=(0, 0, 1), width=2)
        doc.save(str(figs / name))
        doc.close()
    (source / "main.tex").write_text(
        r"""
\documentclass{article}
\title{Multi-Fig Paper}
\author{Ada Lovelace}
\begin{document}
\section{Introduction}
Text here.
\begin{figure}\includegraphics{figs/arch}\caption{Architecture.}\label{fig:arch}\end{figure}
\section{Experiments}
Results here.
\begin{figure}\includegraphics{figs/result}\caption{Results.}\label{fig:result}\end{figure}
\end{document}
""",
        encoding="utf-8",
    )
    artifact = paper_render.build_paper_read_for_pdf(pdf, title="Multi-Fig Paper")
    html_text = open(artifact["outputs"]["presentation"], encoding="utf-8").read()
    # Both placeholders must be present (audit requires this).
    assert 'id="ph-fig1"' in html_text
    assert 'id="ph-fig2"' in html_text
    # With round-robin distribution, fig1→sec0, fig2→sec1, so they are in different sections.
    ph1_pos = html_text.index('id="ph-fig1"')
    ph2_pos = html_text.index('id="ph-fig2"')
    assert ph1_pos != ph2_pos
    assert artifact["audit"]["status"] == "passed"


def test_presentation_renders_without_figures_or_digest(tmp_path):
    content = {
        "title": "Test Paper",
        "authors": "Test Author",
        "sections": [{"id": "sec-1", "title": "Abstract", "level": 1, "page": 1, "text": ""}],
        "figures": [],
        "keywords": ["test"],
        "n_pages": 5,
        "references_count": 12,
        "source_tier": "pdf",
    }
    html_out = _paper_read_html._render_presentation(content, "test_paper")
    assert "Test Paper" in html_out
    # No figures/digest/quality/goals → an explicit empty-state + the PDF footer.
    assert "No readable content could be extracted" in html_out
    assert "open the original PDF in Zotero" in html_out
    # No figures section when there are no figures.
    assert 'id="figures"' not in html_out
    # No digest section when none provided.
    assert 'id="digest"' not in html_out


def test_renderer_rev_folds_into_key_and_flags_currency(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    key = paper_render._pdf_key(pdf.resolve())
    assert key.startswith(f"{paper_render._PAPER_READ_VERSION}:{paper_render._RENDERER_REV}:")
    assert paper_render._key_is_current(key) is True
    # Old 3-part key (pre-renderer-rev) and a different-rev key are stale.
    assert paper_render._key_is_current(f"{paper_render._PAPER_READ_VERSION}:123:456") is False
    assert paper_render._key_is_current(f"{paper_render._PAPER_READ_VERSION}:deadbeef:1:2") is False


def test_render_paper_flags_stale_when_renderer_changed(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)

    class _Reader:
        def get_item_detail(self, key):
            return {"title": "GlassNet", "pdf_path": str(pdf)}

    fake_settings = types.SimpleNamespace(paper_render_dir=tmp_path / "render", pdf_root=tmp_path)
    monkeypatch.setattr(paper_render, "settings", lambda: fake_settings)
    monkeypatch.setattr(
        "zotero_summarizer.services.zotero.zotero.get_zotero_reader_or_raise", lambda: _Reader()
    )
    paper_render.build_paper_read("STALE1")
    assert paper_render.render_paper("STALE1").get("stale") is not True
    # Simulate an artifact built by an older renderer revision.
    state_path = tmp_path / "render" / "STALE1" / "paper_read.json"
    state = json.loads(state_path.read_text())
    state["pdf_key"] = f"{paper_render._PAPER_READ_VERSION}:oldrev00:1:2"
    state_path.write_text(json.dumps(state))
    assert paper_render.render_paper("STALE1")["stale"] is True


def test_tex_tier_persists_pdf_body_for_qa_and_sections(tmp_path):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)
    _make_tex_source(pdf)
    artifact = paper_render.build_paper_read_for_pdf(pdf, title="GlassNet")
    assert artifact["source_tier"] == "local_tex"
    # PDF-extracted body + sections persisted so QA + the brief don't rely on noisy TeX.
    assert "ImageNet" in artifact.get("qa_text", ""), "qa_text (PDF body) missing for TeX tier"
    assert artifact.get("render_sections"), "render_sections (PDF sections) missing for TeX tier"
    # Comprehensive QA context now includes the real body text on a TeX paper.
    assert "ImageNet" in paper_render.artifact_text(artifact, max_chars=100_000)


def test_pdf_figures_dedup_in_prose_caption_mentions(tmp_path):
    from zotero_summarizer.services.library import _paper_read_pdf

    pdf = tmp_path / "dup.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 70), "Dedup Paper", fontsize=18)
    # In-prose mention that also matches the caption regex (no graphic above it).
    page.insert_text((72, 150), "Figure 1 shows the overall pipeline in detail here.", fontsize=10)
    # The real figure: a graphic with its caption below it, lower on the page.
    page.draw_rect(fitz.Rect(120, 300, 430, 430), color=(0, 0, 0), width=2)
    page.draw_line((120, 430), (430, 300), color=(1, 0, 0), width=2)
    page.insert_text((72, 460), "Figure 1: The overall pipeline architecture diagram.", fontsize=9)
    doc.save(str(pdf))
    doc.close()

    figs = _paper_read_pdf.extract_pdf_figures(pdf, tmp_path / "figs")
    labels = [f["label"].lower() for f in figs]
    assert labels.count("figure 1") == 1, f"in-prose 'Figure 1' not deduped: {labels}"
    # The graphic-backed caption wins over the in-prose mention.
    assert "architecture" in figs[0]["caption"].lower()


def test_facade_status_presentation_and_figure_guards(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)

    class _Reader:
        def get_item_detail(self, key):
            return {"title": "GlassNet", "pdf_path": str(pdf)}

    fake_settings = types.SimpleNamespace(paper_render_dir=tmp_path / "render", pdf_root=tmp_path)
    monkeypatch.setattr(paper_render, "settings", lambda: fake_settings)
    monkeypatch.setattr(
        "zotero_summarizer.services.zotero.zotero.get_zotero_reader_or_raise",
        lambda: _Reader(),
    )

    missing = paper_render.render_paper("KEY1")
    assert missing["status"] == "missing"
    built = paper_render.build_paper_read("KEY1")
    assert paper_render.render_paper("KEY1")["status"] == "completed"
    assert paper_render.presentation_path("KEY1").name.endswith("_presentation.html")
    assert paper_render.figure_path("KEY1", built["figures"][0]["name"]).is_file()
    for bad in ("../secret.png", "fig_0.svg", "render.json", "fig_x.png"):
        with pytest.raises(APIError):
            paper_render.figure_path("KEY1", bad)
    state = json.loads((tmp_path / "render" / "KEY1" / "paper_read.json").read_text())
    assert state["pdf_key"] == built["pdf_key"]


def test_missing_presentation_output_is_rebuildable(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _make_pdf(pdf)

    class _Reader:
        def get_item_detail(self, key):
            return {"title": "GlassNet", "pdf_path": str(pdf)}

    fake_settings = types.SimpleNamespace(paper_render_dir=tmp_path / "render", pdf_root=tmp_path)
    monkeypatch.setattr(paper_render, "settings", lambda: fake_settings)
    monkeypatch.setattr(
        "zotero_summarizer.services.zotero.zotero.get_zotero_reader_or_raise",
        lambda: _Reader(),
    )

    built = paper_render.build_paper_read("KEY2")
    presentation = Path(built["outputs"]["presentation"])
    assert presentation.read_text(encoding="utf-8")
    presentation.unlink()

    status = paper_render.render_paper("KEY2")
    assert status["status"] == "missing"
    assert status["stale"] is True
    assert "Generated HTML brief is missing" in status["message"]

    rebuilt = paper_render.build_paper_read("KEY2")
    assert rebuilt["status"] == "completed"
    assert Path(rebuilt["outputs"]["presentation"]).read_text(encoding="utf-8")


def test_build_paper_read_acquires_missing_pdf_when_allowed(tmp_path, monkeypatch):
    acquired = tmp_path / "acquired.pdf"
    _make_pdf(acquired)
    seen: dict = {}

    class _Reader:
        def get_item_detail(self, key):
            return {
                "title": "AgentClinic",
                "pdf_path": "",
                "has_pdf": False,
                "url": "https://www.nature.com/articles/s41746-024-01074-z",
                "doi": "10.1038/s41746-024-01074-z",
            }

    def _acquire(item_key, detail, *, allow_headed_fallback):
        seen.update(
            item_key=item_key,
            url=detail["url"],
            allow_headed_fallback=allow_headed_fallback,
        )
        return types.SimpleNamespace(path=acquired, needs_login=False, login_url="")

    fake_settings = types.SimpleNamespace(paper_render_dir=tmp_path / "render", pdf_root=tmp_path)
    monkeypatch.setattr(paper_render, "settings", lambda: fake_settings)
    monkeypatch.setattr(
        "zotero_summarizer.services.zotero.zotero.get_zotero_reader_or_raise",
        lambda: _Reader(),
    )
    monkeypatch.setattr(
        "zotero_summarizer.services.library._pdf_acquire.acquire_pdf_for",
        _acquire,
    )

    status = paper_render.render_paper("KEY3")
    assert status["status"] == "missing"
    assert status["needs_pdf"] is True

    built = paper_render.build_paper_read("KEY3", allow_acquire_missing=True)
    assert built["status"] == "completed"
    assert built["acquired_pdf"] is True
    assert built["item_key"] == "KEY3"
    assert paper_render.source_pdf_path("KEY3") == acquired.resolve()
    assert Path(built["outputs"]["presentation"]).is_file()
    assert seen == {
        "item_key": "KEY3",
        "url": "https://www.nature.com/articles/s41746-024-01074-z",
        "allow_headed_fallback": True,
    }
