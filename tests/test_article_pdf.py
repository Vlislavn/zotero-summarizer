"""Text-only article PDF conversion must preserve bounded source content."""

from __future__ import annotations

import builtins
from types import SimpleNamespace

import fitz
import pytest

from zotero_summarizer.integrations import _article_pdf


_TEXT_LIMIT_ENV = "ZS_ARTICLE_PDF_MAX_TEXT_BYTES"
_PAGE_LIMIT_ENV = "ZS_ARTICLE_PDF_MAX_PAGES"


def test_article_text_limit_is_finite_env_overridable_and_output_bounded(monkeypatch):
    monkeypatch.delenv(_TEXT_LIMIT_ENV, raising=False)
    monkeypatch.delenv(_PAGE_LIMIT_ENV, raising=False)

    assert _article_pdf.article_text_limit(max_bytes=4_000_000) == 1_000_000
    assert _article_pdf._MAX_TEXT_BYTES == 4_000_000
    assert _article_pdf._positive_int_env(
        _PAGE_LIMIT_ENV, _article_pdf._DEFAULT_MAX_PAGES, _article_pdf._MAX_PAGES
    ) == 256
    assert _article_pdf._MAX_PAGES == 512

    monkeypatch.setenv(_TEXT_LIMIT_ENV, "41")
    assert _article_pdf.article_text_limit(max_bytes=100) == 41
    assert _article_pdf.article_text_limit(max_bytes=32) == 32


@pytest.mark.parametrize("value", ["invalid", "2.5", "0", "-1", "999999999"])
def test_malformed_or_out_of_range_text_limit_fails_loudly(monkeypatch, value):
    monkeypatch.setenv(_TEXT_LIMIT_ENV, value)

    with pytest.raises(ValueError):
        _article_pdf.article_text_limit(max_bytes=100_000)


def test_overbudget_utf8_source_fails_before_opening_native_document(monkeypatch):
    monkeypatch.setenv(_TEXT_LIMIT_ENV, "8")
    monkeypatch.setattr(
        _article_pdf.fitz,
        "open",
        lambda *args, **kwargs: pytest.fail("native PDF document opened before source preflight"),
    )

    with pytest.raises(ValueError, match="UTF-8 source"):
        _article_pdf.render_text_pdf("12345678é", max_bytes=100)


@pytest.mark.parametrize("value", ["invalid", "0", "513"])
def test_malformed_or_out_of_range_page_limit_fails_before_native_document(monkeypatch, value):
    monkeypatch.setenv(_PAGE_LIMIT_ENV, value)
    monkeypatch.setattr(
        _article_pdf.fitz,
        "open",
        lambda *args, **kwargs: pytest.fail("native PDF document opened before page preflight"),
    )

    with pytest.raises(ValueError):
        _article_pdf.render_text_pdf("article", max_bytes=100)


def test_page_budget_fails_before_opening_native_document(monkeypatch):
    monkeypatch.setenv(_PAGE_LIMIT_ENV, "1")
    monkeypatch.setattr(
        _article_pdf.fitz,
        "open",
        lambda *args, **kwargs: pytest.fail("native PDF document opened before page preflight"),
    )

    with pytest.raises(ValueError, match="page limit"):
        _article_pdf.render_text_pdf("long paragraph " * 2_000, max_bytes=100_000)


def test_output_limit_stops_incremental_sink_without_complete_pdf_buffer(monkeypatch):
    class FakeDocument:
        def __init__(self):
            self.sink = None
            self.closed = False

        def new_page(self, **kwargs):
            return SimpleNamespace(insert_text=lambda *args, **kw: None)

        def set_metadata(self, metadata):
            self.metadata = metadata

        def save(self, sink, **kwargs):
            self.sink = sink
            sink.write(b"%PDF-1.7\n")
            sink.write(b"x" * 1_024)

        def close(self):
            self.closed = True

        def tobytes(self, *args, **kwargs):
            pytest.fail("complete PDF serialization must not be materialized")

    document = FakeDocument()
    monkeypatch.setattr(_article_pdf.fitz, "open", lambda *args, **kwargs: document)

    with pytest.raises(ValueError, match="max_bytes"):
        _article_pdf.render_text_pdf("x", max_bytes=16)

    assert document.closed
    assert document.sink is not None
    assert document.sink.tell() <= 16
    assert document.sink.getvalue() == b"%PDF-1.7\n"


def test_real_pymupdf_output_limit_does_not_use_full_document_serializers(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("tobytes()/write() would materialize the complete PDF")

    monkeypatch.setattr(fitz.Document, "tobytes", forbidden)
    monkeypatch.setattr(fitz.Document, "write", forbidden)

    with pytest.raises(ValueError, match="max_bytes"):
        _article_pdf.render_text_pdf("A", max_bytes=64)


def test_text_roundtrip_keeps_ascii_cyrillic_long_paragraph_and_tail_sentinel():
    paragraph = "Длинный абзац: исследование проверяет данные и методы. " * 300
    source = f"ASCII opening. Привет, мир!\n{paragraph}\nLATE_SENTINEL: текст полностью сохранён."

    pdf = _article_pdf.render_text_pdf(source, max_bytes=2_000_000)

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) <= 2_000_000
    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "\n".join(page.get_text() for page in document)

    assert "".join(extracted.split()) == "".join(source.split())
    assert "ASCII opening." in extracted
    assert "Привет, мир!" in extracted
    assert "LATE_SENTINEL: текст полностью сохранён." in extracted


def test_supported_greek_and_cyrillic_roundtrip_through_long_paragraph_tail():
    paragraph = "Длинный абзац содержит методы, результаты и детали. " * 300
    source = f"Greek: αβγ Ω; Cyrillic: Привет, ёжик. {paragraph}LATE_SENTINEL: Ωαβγ, конец."

    pdf = _article_pdf.render_text_pdf(source, max_bytes=2_000_000)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "".join("".join(page.get_text().splitlines()) for page in document)

    assert extracted == source
    assert "αβγ Ω" in extracted
    assert "Привет, ёжик" in extracted
    assert extracted.endswith("LATE_SENTINEL: Ωαβγ, конец.")


def test_greek_accented_iota_marker_roundtrips_without_replacement(monkeypatch):
    monkeypatch.setenv(_TEXT_LIMIT_ENV, "16384")
    monkeypatch.delenv(_PAGE_LIMIT_ENV, raising=False)
    source = "Greek marker: ί"
    assert _article_pdf.article_text_limit(128_000) == 16_384

    pdf = _article_pdf.render_text_pdf(source, max_bytes=128_000)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "".join("".join(page.get_text().splitlines()) for page in document)
        fonts = document.get_page_fonts(0, full=True)

    assert extracted == source
    assert "\u03af" in extracted
    assert any(font[4] == _article_pdf._UNICODE_FONT_NAME for font in fonts)
    assert len(pdf) <= 128_000
    with pytest.raises(ValueError, match="max_bytes"):
        _article_pdf.render_text_pdf(source, max_bytes=len(pdf) - 1)


def test_tabs_render_as_four_spaces_and_other_control_characters_are_rejected():
    pdf = _article_pdf.render_text_pdf("if ready:\n\treturn α", max_bytes=10_000)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "\n".join(page.get_text().rstrip("\n") for page in document).rstrip("\n")

    assert extracted == "if ready:\n    return α"
    with pytest.raises(ValueError, match="unsupported control character"):
        _article_pdf.render_text_pdf("before\x01after", max_bytes=10_000)


def test_unsupported_glyph_is_rejected_instead_of_silently_disappearing(monkeypatch):
    monkeypatch.setattr(
        _article_pdf.fitz,
        "open",
        lambda *args, **kwargs: pytest.fail("glyph validation must precede document creation"),
    )

    with pytest.raises(ValueError, match=r"U\+1F642"):
        _article_pdf.render_text_pdf("unsupported emoji 🙂", max_bytes=10_000)


def test_accented_greek_and_cyrillic_roundtrip_through_embedded_builtin_font():
    source = (
        "Greek: ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ "
        "αβγδεζηθικλμνξοπρστυφχψως ΆΈΉΊΌΎΏ άέήίόύώ; "
        "Cyrillic: АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ "
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя."
    )

    pdf = _article_pdf.render_text_pdf(source, max_bytes=128_000)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "".join("".join(page.get_text().splitlines()) for page in document)

    assert extracted == source
    assert "ί" in extracted
    assert "ЁЖ" in extracted
    assert "ё" in extracted


def test_ascii_only_pdf_uses_base14_without_embedding_unicode_font():
    pdf = _article_pdf.render_text_pdf("ASCII article text", max_bytes=10_000)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        fonts = document.get_page_fonts(0, full=True)

    assert len(pdf) <= 10_000
    assert all(font[4] != _article_pdf._UNICODE_FONT_NAME for font in fonts)


def test_pdf_metadata_discloses_text_only_conversion_without_foreign_assets():
    pdf = _article_pdf.render_text_pdf("Plain text α ί Привет", max_bytes=128_000)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        metadata = document.metadata
        assert metadata["title"] == "Text-only article conversion"
        assert metadata["subject"] == "Text-only conversion; original visual layout is not preserved."
        assert metadata["creator"] == "Zotero Summarizer"
        assert document.embfile_count() == 0
        assert all(not page.get_images(full=True) for page in document)


def test_unicode_rendering_uses_native_subsetting_without_importing_fonttools(monkeypatch):
    original_import = builtins.__import__
    original_subset_fonts = fitz.Document.subset_fonts
    subset_calls = []

    def reject_fonttools(name, *args, **kwargs):
        if name == "fontTools" or name.startswith("fontTools."):
            raise AssertionError(f"Unicode PDF rendering imported {name}")
        return original_import(name, *args, **kwargs)

    def record_native_subsetting(document, *args, **kwargs):
        subset_calls.append((args, kwargs))
        return original_subset_fonts(document, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_fonttools)
    monkeypatch.setattr(fitz.Document, "subset_fonts", record_native_subsetting)
    source = "Greek: ί; Russian: МАРКЕР: текст должен сохраниться."

    pdf = _article_pdf.render_text_pdf(source, max_bytes=128_000)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "".join("".join(page.get_text().splitlines()) for page in document)

    assert extracted == source
    assert subset_calls == [((), {"fallback": False})]


def test_full_accented_greek_and_russian_marker_remain_readable():
    source = (
        "Greek: ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ "
        "αβγδεζηθικλμνξοπρστυφχψως ΆΈΉΊΌΎΏ άέήίόύώ; "
        "Russian marker: МАРКЕР_ПОЛНЫЙ_ТЕКСТ_СОХРАНЁН."
    )

    pdf = _article_pdf.render_text_pdf(source, max_bytes=16_384)

    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "".join("".join(page.get_text().splitlines()) for page in document)

    assert len(pdf) <= 16_384
    assert extracted == source
    assert extracted.endswith("МАРКЕР_ПОЛНЫЙ_ТЕКСТ_СОХРАНЁН.")
