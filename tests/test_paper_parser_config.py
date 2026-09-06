"""The configured parser owns artifact/Q&A text and participates in cache identity."""
from __future__ import annotations

import json
from types import SimpleNamespace

import fitz
import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.library import _paper_docling, _paper_read_pdf, paper_render, qa

_DOCLING_TEXT = "The structured table reports OMEGA as the evaluation dataset."
_FITZ_TEXT = "The text layer reports ALPHA as the evaluation dataset."


@pytest.fixture
def paper(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), _FITZ_TEXT)
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 70, 200, 85),
                          "uri": "https://example.org/code"})
        doc.new_page().insert_text((72, 72), "References\n[1] First reference.")
        doc.set_metadata({"title": "Parser study", "author": "Ada Lovelace", "keywords": "tables; models"})
        doc.save(pdf)
    qr = SimpleNamespace(use_docling=False, max_text_chars=60_000)
    config = SimpleNamespace(quality_review=qr, llm_routing=None)
    get_context().state.app_state = SimpleNamespace(config=config)
    monkeypatch.setattr(paper_render, "settings", lambda: SimpleNamespace(
        paper_render_dir=tmp_path / "state", pdf_root=tmp_path, pdf_cache_dir=tmp_path / "cache",
    ))
    monkeypatch.setattr(paper_render, "_item_detail", lambda _: {"pdf_path": str(pdf), "title": "Parser study"})
    monkeypatch.setattr(paper_render, "_JOBS", {})
    calls = []

    def docling(path):
        calls.append(path)
        return {"full_text": _DOCLING_TEXT + "\nReferences\n[1] First reference.",
                "sections": [{"id": "table-1", "title": "Results", "page": 1, "text": _DOCLING_TEXT}],
                "tables": ["| Dataset |\n|---|\n| OMEGA |"], "figures": []}

    monkeypatch.setattr(_paper_docling, "extract", docling)
    return SimpleNamespace(pdf=pdf, qr=qr, config=config, calls=calls)


@pytest.mark.parametrize("tex", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_build_honors_parser_for_both_source_tiers(paper, tex, enabled):
    paper.qr.use_docling = enabled
    if tex:
        source = paper_render._paper_directory(paper.pdf.resolve()) / "source"
        source.mkdir()
        (source / "main.tex").write_text(r"\title{Parser study}\section{Results}Local TeX content.")
    built = paper_render.build_paper_read("KEY")
    expected = _DOCLING_TEXT if enabled else _FITZ_TEXT
    assert expected in paper_render.qa_body_text(built)
    assert built["source_tier"] == ("local_tex" if tex else "pdf")
    assert built["n_pages"] == 2
    assert built["audit"]["status"] == "passed"
    assert len(paper.calls) == int(enabled)


def test_docling_dispatch_retains_common_pdf_metadata(paper, monkeypatch):
    monkeypatch.setattr(_paper_read_pdf, "_extract_fitz_body", lambda *_: pytest.fail("Docling must own the body"))
    content = _paper_read_pdf.extract_pdf_content(paper.pdf, use_docling=True)
    assert content["n_pages"] == 2
    assert content["title"] == "Parser study"
    assert content["authors"] == "Ada Lovelace"
    assert content["keywords"] == ["tables", "models"]
    assert content["link_uris"] == ["https://example.org/code"]
    assert content["references_count"] == 1
    assert content["tables"] == ["| Dataset |\n|---|\n| OMEGA |"]
    assert _DOCLING_TEXT in content["full_text"]
    assert len(paper.calls) == 1


def test_parser_switch_invalidates_and_rebuilds_the_existing_cache(paper):
    first = paper_render.build_paper_read("KEY")
    paper.qr.use_docling = True
    assert paper_render.render_paper("KEY")["stale"] is True
    second = paper_render.build_paper_read("KEY")
    assert second["pdf_key"] != first["pdf_key"]
    assert _DOCLING_TEXT in paper_render.qa_body_text(second)
    assert paper_render.build_paper_read("KEY") == second
    assert len(paper.calls) == 1
    paper.qr.use_docling = False
    assert paper_render.render_paper("KEY")["stale"] is True
    third = paper_render.build_paper_read("KEY")
    assert third["pdf_key"] == first["pdf_key"]
    assert _FITZ_TEXT in paper_render.qa_body_text(third)


def test_env_config_selects_the_same_parser(paper, monkeypatch):
    from zotero_summarizer.services.config_overrides import apply_env_overrides
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    monkeypatch.setenv("ZS_QUALITY_REVIEW_USE_DOCLING", "true")
    get_context().state.app_state.config = apply_env_overrides(_default_goals_config())
    built = paper_render.build_paper_read("KEY")
    assert _DOCLING_TEXT in paper_render.qa_body_text(built)
    assert len(paper.calls) == 1


def test_uninitialized_runtime_keeps_the_lightweight_standalone_path(paper):
    get_context().state.app_state = None
    built = paper_render.build_paper_read_for_pdf(paper.pdf)
    assert _FITZ_TEXT in paper_render.qa_body_text(built)
    assert paper.calls == []


def test_docling_adapter_source_participates_in_renderer_revision(paper, tmp_path, monkeypatch):
    before = paper_render._compute_renderer_rev()
    changed = tmp_path / "changed_docling.py"
    changed.write_text("# changed extractor implementation")
    monkeypatch.setattr(_paper_docling, "__file__", str(changed))
    assert paper_render._compute_renderer_rev() != before


@pytest.mark.parametrize("failure", [OSError("parser failed"), ModuleNotFoundError("docling is missing")])
def test_enabled_parser_failure_is_not_replaced_with_fitz(paper, monkeypatch, failure):
    paper.qr.use_docling = True

    def fail(_path):
        raise failure

    monkeypatch.setattr(_paper_docling, "extract", fail)
    with pytest.raises(type(failure), match=str(failure)):
        paper_render.build_paper_read("KEY")
    assert paper_render._read_state("KEY") is None
    assert not list(paper.pdf.parent.rglob("*_presentation.html"))


def test_changed_parser_during_build_cannot_publish_completed_state(paper, monkeypatch):
    original = paper_render.build_paper_read_for_pdf

    def changed(*args, **kwargs):
        artifact = original(*args, **kwargs)
        paper.qr.use_docling = True
        return artifact

    monkeypatch.setattr(paper_render, "build_paper_read_for_pdf", changed)
    with pytest.raises(APIError) as caught:
        paper_render.build_paper_read("KEY")
    assert caught.value.error == "source_changed"
    assert paper_render._read_state("KEY") is None


@pytest.mark.parametrize("mode", qa.MODES)
def test_qa_rebuilds_for_the_selected_parser_before_model_call(paper, monkeypatch, mode):
    first = paper_render.build_paper_read("KEY")
    paper.qr.use_docling = True
    prompts = []

    class Model:
        def prompt(self, prompt, **kwargs):
            prompts.append(prompt)
            return json.dumps({"answer": "OMEGA", "quote": _DOCLING_TEXT})

    monkeypatch.setattr(qa, "state", lambda: SimpleNamespace(
        app_state=SimpleNamespace(config=paper.config), resolve_stage_client=lambda stage: Model(),
    ))
    monkeypatch.setattr(qa, "resolve_stage", lambda *args: SimpleNamespace(model="fake"))
    answer = qa.ask_paper("KEY", "Which dataset is used?", mode=mode)
    assert answer["answer"] == "OMEGA"
    assert _DOCLING_TEXT in prompts[-1] and _FITZ_TEXT not in prompts[-1]
    assert answer["evidence_handle"]["extraction_version"] != first["pdf_key"]
    assert len(paper.calls) == 1
