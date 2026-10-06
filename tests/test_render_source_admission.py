import pytest

from test_source_admission import DENIAL
from test_paper_render import _make_pdf, _make_tex_source
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import paper_render
from zotero_summarizer.services.library._paper_read_meta import qa_body_text


def test_cached_operational_pdf_cannot_be_used_for_qa():
    with pytest.raises(APIError):
        qa_body_text({'full_text': DENIAL})


@pytest.mark.parametrize('empty', ['', None])
def test_explicit_empty_qa_does_not_fall_back_to_tex(empty):
    assert qa_body_text({'qa_text': empty, 'full_text': 'TeX presentation'}) == ''


def test_good_tex_presentation_survives_bad_pdf_without_qa_leak(tmp_path, monkeypatch):
    pdf = tmp_path / 'paper.pdf'
    _make_pdf(pdf)
    _make_tex_source(pdf)
    monkeypatch.setattr(paper_render._paper_read_pdf, 'extract_pdf_content', lambda *a, **k: {
        'full_text': DENIAL, 'sections': [], 'n_pages': 1,
    })
    artifact = paper_render.build_paper_read_for_pdf(pdf)
    assert artifact['source_tier'] == 'local_tex'
    assert artifact['status'] == 'completed'
    assert artifact['qa_text'] == ''
    assert DENIAL not in artifact['full_text']
    assert artifact['qa_diagnostic']['code'] == 'source_unusable'


def test_pdf_presentation_rejected_before_artifact(tmp_path, monkeypatch):
    pdf = tmp_path / 'paper.pdf'
    _make_pdf(pdf)
    monkeypatch.setattr(paper_render._paper_read_pdf, 'extract_pdf_content', lambda *a, **k: {
        'full_text': DENIAL, 'sections': [], 'n_pages': 1,
    })
    with pytest.raises(APIError):
        paper_render.build_paper_read_for_pdf(pdf)
