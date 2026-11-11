from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_HASH_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class PdfSource:
    text: str
    annotation_uris: tuple[str, ...]
    document_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not isinstance(self.annotation_uris, tuple) or any(
            not isinstance(uri, str) for uri in self.annotation_uris
        ):
            raise TypeError("PDF source requires text and an exact string tuple")
        if not isinstance(self.document_sha256, str):
            raise TypeError("PDF source requires a SHA-256 string")
        if len(self.document_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.document_sha256):
            raise ValueError("PDF source requires a full SHA-256 identity")


class PdfExtractor(Protocol):
    def extract_text(self, pdf_path: str | Path) -> str:
        ...


def raw_link_uris(doc) -> tuple[str, ...]:
    """Return SDK LINK_URI strings verbatim, including order and duplicates."""
    import fitz

    uris = []
    for page in doc:
        links = page.get_links()
        if not isinstance(links, list):
            raise TypeError("PDF links must be a list")
        for link in links:
            if not isinstance(link, dict) or type(link.get("kind")) is not int:
                raise TypeError("PDF link requires an integer kind")
            if link["kind"] == fitz.LINK_URI:
                uri = link.get("uri")
                if not isinstance(uri, str):
                    raise TypeError("PDF LINK_URI requires a string")
                uris.append(uri)
    return tuple(uris)


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _file_fingerprint(path: Path) -> tuple[tuple[int, int, int, int], str]:
    """Hash only the opening size plus one growth-detection byte, not growing EOF."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opening = _identity(os.fstat(stream.fileno()))
        if _identity(path.stat()) != opening:
            raise ValueError("PDF source identity changed before hashing")
        remaining = opening[2]
        while remaining:
            chunk = stream.read(min(_HASH_CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError("PDF source shrank while hashing")
            digest.update(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise ValueError("PDF source grew while hashing")
        if _identity(os.fstat(stream.fileno())) != opening or _identity(path.stat()) != opening:
            raise ValueError("PDF source identity changed while hashing")
    return opening, digest.hexdigest()


def capture_pdf_source(pdf_path: Path, extractor: PdfExtractor) -> PdfSource:
    """Capture selected text and raw annotations from an ordinarily stable original."""
    import fitz

    before = _file_fingerprint(pdf_path)
    text = extractor.extract_text(pdf_path)
    with fitz.open(pdf_path) as doc:
        uris = raw_link_uris(doc)
    if _file_fingerprint(pdf_path) != before:
        raise ValueError("PDF source changed during extraction")
    return PdfSource(text, uris, before[1])


class OnPremPdfExtractor:
    """PDF extractor adapter around `onprem.ingest.base.load_single_document`."""

    def __init__(self, load_single_document) -> None:
        self._load_single_document = load_single_document

    def extract_text(self, pdf_path: str | Path) -> str:
        docs = self._load_single_document(str(pdf_path), pdf_markdown=False)
        parts = []
        for doc in docs:
            text = getattr(doc, "page_content", None)
            if text:
                parts.append(str(text))
        return "\n\n".join(parts).strip()
