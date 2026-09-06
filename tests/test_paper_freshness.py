"""A paper's current source, extracted text and evidence version must agree."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _pdf_acquire, app_library_reader, deep_review, paper_render, qa
from zotero_summarizer.services.zotero import zotero

_FEED = "feed:d:" + "a" * 64
_SENTENCE = "Results use dataset ALPHA in every evaluation."


def _replace_dataset(pdf):
    before = pdf.stat()
    original = pdf.read_bytes()
    old, new = b"ALPHA".hex().encode(), b"OMEGA".hex().encode()
    assert old in original
    pdf.write_bytes(original.replace(old, new))
    os.utime(pdf, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert pdf.stat().st_size == before.st_size
    assert pdf.stat().st_mtime_ns == before.st_mtime_ns
    with fitz.open(pdf) as doc:
        assert "OMEGA" in doc[0].get_text()


@pytest.fixture
def paper(tmp_path, monkeypatch):
    config = SimpleNamespace(
        paper_render_dir=tmp_path / "render", pdf_root=tmp_path / "library",
        pdf_cache_dir=tmp_path / "cache", triage_db_path=tmp_path / "triage.sqlite",
    )
    config.pdf_root.mkdir()
    config.pdf_cache_dir.mkdir()
    pdf = config.pdf_root / "paper.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), _SENTENCE)
        doc.save(pdf, expand=255)  # uncompressed stream for a same-length byte replacement
    detail = {"title": "Dataset study", "pdf_path": str(pdf), "has_pdf": True}
    reader = SimpleNamespace(get_item_detail=lambda key: None if key == _FEED else detail)
    feed_reader = SimpleNamespace(get_item_detail=lambda key: {"title": "Feed study", "pdf_path": None})
    monkeypatch.setattr(zotero, "get_library_reader", lambda app=None: reader)
    monkeypatch.setattr(zotero, "settings", lambda: config)
    monkeypatch.setattr(app_library_reader, "AppLibraryReader", lambda path: feed_reader)
    monkeypatch.setattr(paper_render, "settings", lambda: config)
    monkeypatch.setattr(paper_render, "_JOBS", {})
    monkeypatch.setattr(deep_review, "get_cached_review", lambda key: None)
    return SimpleNamespace(config=config, pdf=pdf, detail=detail)


def test_same_size_same_timestamp_content_replacement_changes_key(paper):
    key = paper_render._pdf_key(paper.pdf)
    _replace_dataset(paper.pdf)
    assert paper_render._pdf_key(paper.pdf) != key


def test_touch_does_not_invalidate_identical_content(paper):
    key = paper_render._pdf_key(paper.pdf)
    stat = paper.pdf.stat()
    os.utime(paper.pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert paper_render._pdf_key(paper.pdf) == key


@pytest.mark.parametrize("change", ["content", "attachment_path"])
def test_status_detects_source_changes_and_build_reuses_only_current_artifact(paper, change):
    built = paper_render.build_paper_read("KEY")
    assert not paper_render.render_paper("KEY").get("stale")
    assert paper_render.build_paper_read("KEY") == built
    if change == "content":
        _replace_dataset(paper.pdf)
    else:
        moved = paper.pdf.with_name("other.pdf")
        moved.write_bytes(paper.pdf.read_bytes())
        stat = paper.pdf.stat()
        os.utime(moved, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        paper.detail["pdf_path"] = str(moved)
    assert paper_render.render_paper("KEY")["stale"] is True
    rebuilt = paper_render.build_paper_read("KEY")
    assert rebuilt["pdf_key"] != built["pdf_key"]
    assert rebuilt["pdf_path"] == paper.detail["pdf_path"]
    assert not paper_render.render_paper("KEY").get("stale")
    if change == "content":
        assert "OMEGA" in paper_render.qa_body_text(rebuilt)
        assert "ALPHA" not in paper_render.qa_body_text(rebuilt)


def test_build_rejects_source_change_during_extraction(paper, monkeypatch):
    original = paper_render.build_paper_read_for_pdf

    def changed(*args, **kwargs):
        artifact = original(*args, **kwargs)
        _replace_dataset(paper.pdf)
        return artifact

    monkeypatch.setattr(paper_render, "build_paper_read_for_pdf", changed)
    with pytest.raises(APIError) as caught:
        paper_render.build_paper_read("KEY")
    assert caught.value.status_code == 409
    assert caught.value.error == "source_changed"
    assert paper_render._read_state("KEY") is None


def _qa_app(monkeypatch):
    prompts = []

    class Model:
        def prompt(self, prompt, **kwargs):
            prompts.append(prompt)
            word = "OMEGA" if "OMEGA" in prompt else "ALPHA"
            return json.dumps({"answer": word, "quote": _SENTENCE.replace("ALPHA", word)})

    class ExtraExtractor:
        def extract_text(self, path):
            pytest.fail("Q&A must use its versioned artifact, not a second PDF extraction")

    app = SimpleNamespace(
        pdf_extractor=ExtraExtractor(),
        app_state=SimpleNamespace(config=SimpleNamespace(
            quality_review=SimpleNamespace(max_text_chars=60_000), llm_routing=None,
        )),
        resolve_stage_client=lambda stage: Model(),
    )
    monkeypatch.setattr(qa, "state", lambda: app)
    monkeypatch.setattr(qa, "resolve_stage", lambda *args: SimpleNamespace(model="fake"))
    return prompts


@pytest.mark.parametrize("mode", qa.MODES)
def test_all_qa_modes_use_current_artifact_text_and_evidence_version(paper, monkeypatch, mode):
    prompts = _qa_app(monkeypatch)
    original = qa.ask_paper("KEY", "Which dataset is used?")
    assert original["answer"] == "ALPHA"
    _replace_dataset(paper.pdf)
    current = qa.ask_paper("KEY", "Which dataset is used?", mode=mode)
    assert current["answer"] == "OMEGA"
    assert "OMEGA" in prompts[-1] and "ALPHA" not in prompts[-1]
    assert current["citation"]["location_verified"] is True
    assert current["evidence_handle"]["extraction_version"] != original["evidence_handle"]["extraction_version"]


@pytest.mark.parametrize("mode", qa.MODES)
def test_feed_qa_reuses_acquired_source_with_zotero_present(paper, monkeypatch, mode):
    cached = paper.config.pdf_cache_dir / "feed.pdf"
    cached.write_bytes(paper.pdf.read_bytes())
    calls = []

    def acquire(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(path=cached)

    monkeypatch.setattr(_pdf_acquire, "acquire_pdf_for", acquire)
    paper_render.build_paper_read(_FEED, allow_acquire_missing=True)
    _replace_dataset(cached)
    _qa_app(monkeypatch)
    current = qa.ask_paper(_FEED, "Which dataset is used?", mode=mode)
    assert current["answer"] == "OMEGA"
    assert current["citation"]["location_verified"] is True
    assert calls == [_FEED]  # only the explicit initial acquisition
    assert paper_render._read_state(_FEED)["acquired_pdf"] is True
    assert not paper_render.render_paper(_FEED).get("stale")


def test_reader_can_resolve_an_existing_project_cache_pdf(paper):
    cached = paper.config.pdf_cache_dir / "resolved.pdf"
    cached.write_bytes(paper.pdf.read_bytes())
    paper.detail["pdf_path"] = str(cached)
    artifact = paper_render.build_paper_read("KEY")
    assert Path(artifact["pdf_path"]) == cached


def test_source_identity_is_checked_after_waiting_for_build_lock(paper, monkeypatch):
    paper_render.build_paper_read("KEY")

    @contextmanager
    def waiting(key):
        _replace_dataset(paper.pdf)
        yield

    monkeypatch.setattr(paper_render, "_get_item_lock", waiting)
    artifact = paper_render.build_paper_read("KEY")
    assert "OMEGA" in paper_render.qa_body_text(artifact)


def test_legacy_timestamp_key_is_stale_even_with_current_renderer(paper):
    built = paper_render.build_paper_read("KEY")
    stat = paper.pdf.stat()
    built["pdf_key"] = f"paper-read-v1:{paper_render._RENDERER_REV}:{int(stat.st_mtime)}:{stat.st_size}"
    paper_render._write_state("KEY", built)
    assert paper_render.render_paper("KEY")["stale"] is True
    assert paper_render.build_paper_read("KEY")["pdf_key"] != built["pdf_key"]


def test_renderer_revision_does_not_hide_unreadable_source(monkeypatch):
    def unreadable(path):
        raise OSError("renderer source unavailable")

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(OSError, match="renderer source unavailable"):
        paper_render._compute_renderer_rev()


@pytest.mark.parametrize("mode", ["retrieval", "full_text"])
def test_empty_artifact_body_fails_before_resolving_model(monkeypatch, mode):
    monkeypatch.setattr(paper_render, "build_paper_read", lambda key: {"full_text": " "})
    _qa_app(monkeypatch)
    qa.state().resolve_stage_client = lambda stage: pytest.fail("empty body must fail before model resolution")
    with pytest.raises(APIError) as caught:
        qa.ask_paper("KEY", "Which dataset?", mode=mode)
    assert caught.value.error == "extraction_empty"
