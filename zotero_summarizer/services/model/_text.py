"""Plain scientific text for the encoder; raw source records stay untouched."""
from __future__ import annotations

from html.parser import HTMLParser


class _PaperText(HTMLParser):
    _BLOCKS = {"p", "div", "br", "li", "ul", "ol", "section", "abstract", "jats:p",
               "h1", "h2", "h3", "h4", "tr", "td"}
    _HIDDEN = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._HIDDEN:
            self.hidden = tag
        elif tag in self._BLOCKS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == self.hidden:
            self.hidden = None
        elif tag in self._BLOCKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.hidden is None:
            self.parts.append(data)


def paper_text(value: str) -> str:
    """Remove markup, decode entities and retain prose, numbers and inequalities."""
    parser = _PaperText()
    parser.feed(value or "")
    parser.close()
    return " ".join("".join(parser.parts).split())
