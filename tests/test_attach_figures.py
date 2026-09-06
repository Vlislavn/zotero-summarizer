"""Attach extracted figures to a Zotero item — the write-primitive generalization
(content-type-aware filename) + the figure enumeration that feeds it."""
from __future__ import annotations

from types import SimpleNamespace

from zotero_summarizer.integrations._zotero_write_attachments import ZoteroAttachmentWriteMixin
from zotero_summarizer.services.library import paper_figures, paper_render


def test_safe_attachment_filename_forces_content_type_extension() -> None:
    f = ZoteroAttachmentWriteMixin._safe_attachment_filename
    assert f("fig1_intro", "image/png") == "fig1_intro.png"
    assert f("fulltext", "application/pdf") == "fulltext.pdf"
    # already-correct extension is not doubled
    assert f("fig1_intro.png", "image/png") == "fig1_intro.png"
    # empty name → the fixed stem with the type's extension
    assert f("", "image/png") == "fulltext.png"
    # path separators are stripped (no directory traversal into storage/)
    assert "/" not in f("../evil/name.png", "image/png")
    assert "\\" not in f("a\\b.png", "image/png")


def test_attachable_figures_returns_only_audited_images(tmp_path, monkeypatch) -> None:
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1_intro.png").write_bytes(b"x")
    (figs / "fig2_method.png").write_bytes(b"x")
    (figs / "notes.txt").write_text("nope")   # not an image
    (figs / "random.png").write_bytes(b"x")    # doesn't match the fig<n>_ allowlist
    monkeypatch.setattr(
        paper_render, "settings", lambda: SimpleNamespace(
            paper_render_dir=tmp_path / "render", pdf_root=tmp_path, pdf_cache_dir=tmp_path / "cache",
        ),
    )
    paper_render._write_state("ANYKEY", {
        "status": "completed", "audit": {"status": "passed", "blocking": []},
        "outputs": {"figures_dir": str(figs)},
        "figures": [{"name": "fig1_intro.png"}, {"name": "fig2_method.png"}],
    })
    out = paper_figures.attachable_figures("ANYKEY")
    assert sorted(f["name"] for f in out) == ["fig1_intro.png", "fig2_method.png"]
    assert all(f["path"].endswith(f["name"]) for f in out)


def test_attachable_figures_empty_without_state(monkeypatch) -> None:
    monkeypatch.setattr(paper_figures, "_read_state", lambda key: None)
    assert paper_figures.attachable_figures("X") == []
