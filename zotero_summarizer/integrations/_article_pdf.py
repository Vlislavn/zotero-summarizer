"""Bounded, text-only PDF rendering for web articles."""
from __future__ import annotations

import io
import os
import unicodedata
from collections.abc import Iterator
from functools import lru_cache
from itertools import zip_longest

import fitz


_TEXT_LIMIT_ENV = "ZS_ARTICLE_PDF_MAX_TEXT_BYTES"
_PAGE_LIMIT_ENV = "ZS_ARTICLE_PDF_MAX_PAGES"
_DEFAULT_TEXT_BYTES = 1_000_000
_MAX_TEXT_BYTES = 4_000_000
_DEFAULT_MAX_PAGES = 256
_MAX_PAGES = 512
_PAGE_WIDTH_PT = 612.0
_PAGE_HEIGHT_PT = 792.0
_MARGIN_PT = 48.0
_FONT_NAME = "helv"
_UNICODE_FONT_NAME = "article-unicode"
_UNICODE_BUILTIN_FONT_NAME = "cjk"
_TAB_SPACES = "    "
_PDF_METADATA = {
    "title": "Text-only article conversion",
    "subject": "Text-only conversion; original visual layout is not preserved.",
    "creator": "Zotero Summarizer",
}
_FONT_SIZE_PT = 8.0
_LINE_HEIGHT_PT = 11.0
_LINES_PER_PAGE = 60
_MAX_LINE_CHARS = 56


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{name} must be a positive decimal integer")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive decimal integer") from exc
    if not 0 < value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _validate_max_bytes(max_bytes: int) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    return max_bytes


def article_text_limit(max_bytes: int) -> int:
    """Return the finite UTF-8 source budget, bounded by the PDF output limit."""
    output_limit = _validate_max_bytes(max_bytes)
    configured_limit = _positive_int_env(_TEXT_LIMIT_ENV, _DEFAULT_TEXT_BYTES, _MAX_TEXT_BYTES)
    return min(configured_limit, output_limit)


def _utf8_size(text: str, limit: int) -> int:
    size = 0
    for char in text:
        codepoint = ord(char)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("text contains a character that is not valid UTF-8")
        size += 1 if codepoint <= 0x7F else 2 if codepoint <= 0x7FF else 3 if codepoint <= 0xFFFF else 4
        if size > limit:
            raise ValueError(f"UTF-8 source exceeds text budget of {limit} bytes")
    return size


def _paragraphs(text: str) -> Iterator[str]:
    start = 0
    index = 0
    while index < len(text):
        if text[index] not in "\r\n":
            index += 1
            continue
        yield text[start:index]
        if text[index] == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            index += 1
        index += 1
        start = index
    yield text[start:]


def _append_line(lines: list[str], line: str, maximum: int) -> None:
    if len(lines) >= maximum:
        raise ValueError("text exceeds configured page limit")
    lines.append(line)


@lru_cache(maxsize=1)
def _unicode_font() -> fitz.Font:
    return fitz.Font(fontname=_UNICODE_BUILTIN_FONT_NAME)


def _layout_lines(text: str, maximum_pages: int) -> list[str]:
    unicode_font = None
    maximum_lines = maximum_pages * _LINES_PER_PAGE
    lines: list[str] = []

    for paragraph in _paragraphs(text):
        if not paragraph:
            _append_line(lines, "", maximum_lines)
            continue
        paragraph = paragraph.replace("\t", _TAB_SPACES)
        current: list[str] = []
        for char in paragraph:
            category = unicodedata.category(char)
            if category in {"Cc", "Cf"}:
                raise ValueError("text contains an unsupported control character")
            glyph = ord(char)
            if glyph > 0x7F:
                if unicode_font is None:
                    unicode_font = _unicode_font()
                if not unicode_font.has_glyph(glyph):
                    raise ValueError(f"text contains a character unsupported by the built-in font: U+{glyph:04X}")
            if len(current) >= _MAX_LINE_CHARS:
                _append_line(lines, "".join(current), maximum_lines)
                current = []
            current.append(char)
        _append_line(lines, "".join(current), maximum_lines)

    return lines


class _BoundedPDFSink(io.BytesIO):
    def __init__(self, max_bytes: int):
        super().__init__()
        self.max_bytes = max_bytes
        self.exceeded = False
        self._written = 0

    def write(self, data: bytes) -> int:
        if self._written + len(data) > self.max_bytes:
            self.exceeded = True
            raise ValueError("rendered text PDF exceeds max_bytes")
        written = super().write(data)
        self._written += written
        return written


def _write_layout(document: fitz.Document, lines: list[str], unicode_font: bytes | None) -> None:
    for page_start in range(0, len(lines), _LINES_PER_PAGE):
        page = document.new_page(width=_PAGE_WIDTH_PT, height=_PAGE_HEIGHT_PT)
        page_lines = lines[page_start : page_start + _LINES_PER_PAGE]
        if unicode_font and any(not line.isascii() for line in page_lines):
            page.insert_font(fontname=_UNICODE_FONT_NAME, fontbuffer=unicode_font)
        for line_index, line in enumerate(page_lines):
            if line:
                fontname = _UNICODE_FONT_NAME if not line.isascii() else _FONT_NAME
                page.insert_text(
                    (_MARGIN_PT, _MARGIN_PT + _FONT_SIZE_PT + line_index * _LINE_HEIGHT_PT),
                    line,
                    fontsize=_FONT_SIZE_PT,
                    fontname=fontname,
                    color=(0, 0, 0),
                )


def _verify_text_roundtrip(pdf: bytes, expected: str) -> None:
    with fitz.open(stream=pdf, filetype="pdf") as document:
        extracted = "".join("".join(page.get_text().splitlines()) for page in document)
    for expected_char, extracted_char in zip_longest(expected, extracted):
        if expected_char != extracted_char:
            codepoint = f"U+{ord(expected_char):04X}" if expected_char else "unexpected extracted text"
            raise ValueError(f"PDF text extraction did not preserve source character {codepoint}")


def render_text_pdf(text: str, *, max_bytes: int) -> bytes:
    """Apply source/page/output budgets, not a browser RAM sandbox.

    Tabs map to four spaces; every other control character is rejected.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    source_limit = article_text_limit(max_bytes)
    output_limit = max_bytes
    _utf8_size(text, source_limit)
    if not text.strip():
        raise ValueError("text must contain printable content")
    maximum_pages = _positive_int_env(_PAGE_LIMIT_ENV, _DEFAULT_MAX_PAGES, _MAX_PAGES)
    lines = _layout_lines(text, maximum_pages)
    if not lines:
        raise ValueError("text must contain printable content")

    unicode_font = _unicode_font().buffer if any(not line.isascii() for line in lines) else None
    document = fitz.open()
    try:
        document.set_metadata(_PDF_METADATA)
        _write_layout(document, lines, unicode_font)
        if unicode_font:
            document.subset_fonts(fallback=False)
        sink = _BoundedPDFSink(output_limit)
        try:
            document.save(sink, garbage=0, deflate=1, no_new_id=1)
        except Exception as exc:
            if sink.exceeded:
                raise ValueError("rendered text PDF exceeds max_bytes") from exc
            raise
        pdf = sink.getvalue()
        if not pdf.startswith(b"%PDF-"):
            raise ValueError("PyMuPDF produced an invalid PDF")
        _verify_text_roundtrip(pdf, "".join(lines))
        return pdf
    finally:
        document.close()
