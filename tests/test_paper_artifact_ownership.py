"""Two PDFs in a flat library/cache must never share source or output files."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import fitz
import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _paper_read_tex, paper_render


def _pdf(path, color=(1, 0, 0)):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 70), "Shared First Four Title Words", fontsize=18)
        page.insert_text((72, 95), "arXiv:2401.00001v2", fontsize=9)
        page.insert_text((72, 130), "Abstract", fontsize=14)
        page.insert_text((72, 155), "This paper evaluates a synthetic dataset.", fontsize=10)
        page.draw_rect(fitz.Rect(120, 300, 420, 430), color=color, fill=color)
        page.insert_text((72, 455), "Figure 1: Architecture diagram.", fontsize=9)
        doc.save(path)
    return path


def _directory(artifact):
    return Path(artifact["outputs"]["presentation"]).parent


def _snapshot(directory):
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def _source(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "main.tex").write_text(
        r"\documentclass{article}\title{Only Paper Alpha}\begin{document}"
        r"\section{Methods}Exclusive Alpha source.\end{document}", encoding="utf-8",
    )
    return directory


def test_flat_directory_outputs_and_rebuilds_are_pdf_owned(tmp_path):
    a, b = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf", (0, 0, 1))
    first = paper_render.build_paper_read_for_pdf(a, title="Shared First Four Title Alpha")
    saved = _snapshot(_directory(first))
    second = paper_render.build_paper_read_for_pdf(b, title="Shared First Four Title Beta")

    assert _directory(first) != _directory(second)
    assert _snapshot(_directory(first)) == saved
    for artifact in (first, second):
        output = artifact["outputs"]
        directory = _directory(artifact)
        assert directory.parent == tmp_path.resolve()
        assert Path(output["audit"]).parent == directory
        assert Path(output["figures_dir"]) == directory / "figures"
        assert json.loads(Path(output["audit"]).read_text())["status"] == "passed"
        assert artifact["figures_count"] == 1
        figure = artifact["figures"][0]["name"]
        assert (directory / "figures" / figure).is_file()
        assert f"figures/{figure}" in Path(output["presentation"]).read_text()
    a_image = Path(first["outputs"]["figures_dir"]) / first["figures"][0]["name"]
    b_image = Path(second["outputs"]["figures_dir"]) / second["figures"][0]["name"]
    assert a_image.read_bytes() != b_image.read_bytes()
    saved_b = _snapshot(_directory(second))
    rebuilt = paper_render.build_paper_read_for_pdf(a, title="A renamed paper")
    assert rebuilt["outputs"] == first["outputs"]
    assert _snapshot(_directory(second)) == saved_b


@pytest.mark.parametrize("legacy", ["source", "tex", "latex", "."])
def test_ambiguous_neighbor_tex_is_not_assigned_to_a_pdf(tmp_path, legacy):
    pdf = _pdf(tmp_path / "b.pdf")
    source = _source(tmp_path / legacy)
    original = (source / "main.tex").read_bytes()
    artifact = paper_render.build_paper_read_for_pdf(pdf)
    assert artifact["source_tier"] == "pdf"
    assert "Only Paper Alpha" not in artifact["title"]
    assert (source / "main.tex").read_bytes() == original


def test_explicit_local_source_is_only_used_by_its_pdf(tmp_path):
    a, b = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    first = paper_render.build_paper_read_for_pdf(a)
    source = _source(_directory(first) / "source" / "nested")
    second = paper_render.build_paper_read_for_pdf(b)
    rebuilt = paper_render.build_paper_read_for_pdf(a)
    assert second["source_tier"] == "pdf"
    assert rebuilt["source_tier"] == "local_tex"
    assert rebuilt["title"] == "Only Paper Alpha"
    assert rebuilt["source_dir"] == str(source.parent)
    assert rebuilt["tex_files"] == [str(source / "main.tex")]


def test_arxiv_source_is_only_downloaded_into_its_pdf_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("ZS_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    a, b = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    buffer = io.BytesIO()
    body = b"\\documentclass{article}\\title{Downloaded Alpha}\\section{Methods}Alpha only."
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("main.tex")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    urls = []

    def download(url):
        urls.append(url)
        return buffer.getvalue()

    monkeypatch.setattr(_paper_read_tex, "_download_capped", download)
    first = paper_render.build_paper_read_for_pdf(a, allow_arxiv_source=True)
    second = paper_render.build_paper_read_for_pdf(b)
    assert first["source_tier"] == "arxiv_tex"
    assert Path(first["source_dir"]) == _directory(first) / "source"
    assert second["source_tier"] == "pdf"
    assert len(urls) == 1
    assert not (tmp_path / "source").exists()


def test_empty_owned_source_does_not_block_a_download_retry(tmp_path, monkeypatch):
    monkeypatch.delenv("ZS_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    pdf = _pdf(tmp_path / "a.pdf")
    first = paper_render.build_paper_read_for_pdf(pdf)
    (_directory(first) / "source").mkdir()
    calls = []
    monkeypatch.setattr(_paper_read_tex, "_download_capped", lambda url: calls.append(url))
    for _ in range(2):
        assert paper_render.build_paper_read_for_pdf(pdf, allow_arxiv_source=True)["source_tier"] == "pdf"
    assert len(calls) == 2


@pytest.mark.parametrize("relative", [
    ".", "source", "source/main.tex", "figures", "figures/fig1_architecture.png",
    "paper_presentation.html", "paper_audit.json",
])
def test_existing_artifact_symlinks_are_rejected_before_writes(tmp_path, relative):
    pdf = _pdf(tmp_path / "a.pdf")
    first = paper_render.build_paper_read_for_pdf(pdf)
    directory = _directory(first)
    assert directory.parent == tmp_path.resolve()
    target = tmp_path / "unrelated"
    target.mkdir()
    (target / "keep").write_bytes(b"untouched")
    link = directory / relative
    if link.exists():
        link.rename(tmp_path / "saved-original")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    before = _snapshot(target)
    with pytest.raises(APIError, match="symlink"):
        paper_render.build_paper_read_for_pdf(pdf)
    assert _snapshot(target) == before


def test_long_unicode_pdf_names_keep_distinct_bounded_artifact_paths(tmp_path):
    directories = []
    for suffix in ("a", "b"):
        pdf = _pdf(tmp_path / ("📄" * 60 + suffix + ".pdf"))
        artifact = paper_render.build_paper_read_for_pdf(pdf)
        directory = _directory(artifact)
        assert len(directory.name.encode("utf-8")) <= 255
        directories.append(directory)
    assert directories[0] != directories[1]
