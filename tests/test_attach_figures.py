"""Attach extracted figures to a Zotero item — the write-primitive generalization
(content-type-aware filename) + the figure enumeration that feeds it."""
from __future__ import annotations

from zotero_summarizer.integrations._zotero_write_attachments import ZoteroAttachmentWriteMixin
from zotero_summarizer.services.library import paper_figures


def test_safe_attachment_filename_forces_content_type_extension() -> None:
    f = ZoteroAttachmentWriteMixin._safe_attachment_filename
    assert f("fig1_intro", "image/png", fallback="x") == "fig1_intro.png"
    assert f("fulltext", "application/pdf", fallback="x") == "fulltext.pdf"
    # already-correct extension is not doubled
    assert f("fig1_intro.png", "image/png", fallback="x") == "fig1_intro.png"
    # empty name → fallback (with the type's extension)
    assert f("", "image/png", fallback="fallback") == "fallback.png"
    # path separators are stripped (no directory traversal into storage/)
    assert "/" not in f("../evil/name.png", "image/png", fallback="x")
    assert "\\" not in f("a\\b.png", "image/png", fallback="x")


def test_attachable_figures_scans_only_valid_figure_images(tmp_path, monkeypatch) -> None:
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1_intro.png").write_bytes(b"x")
    (figs / "fig2_method.png").write_bytes(b"x")
    (figs / "notes.txt").write_text("nope")   # not an image
    (figs / "random.png").write_bytes(b"x")    # doesn't match the fig<n>_ allowlist
    monkeypatch.setattr(
        paper_figures, "_read_state",
        lambda key: {"outputs": {"figures_dir": str(figs)}},
    )
    out = paper_figures.attachable_figures("ANYKEY")
    assert sorted(f["name"] for f in out) == ["fig1_intro.png", "fig2_method.png"]
    assert all(f["path"].endswith(f["name"]) for f in out)


def test_attachable_figures_empty_without_state(monkeypatch) -> None:
    monkeypatch.setattr(paper_figures, "_read_state", lambda key: None)
    assert paper_figures.attachable_figures("X") == []
