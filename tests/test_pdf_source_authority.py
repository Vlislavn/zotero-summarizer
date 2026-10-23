"""Neutral SDK capture and finite full-file identity; owned synthetic inputs only."""
import builtins
import hashlib
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fitz
import pytest

from zotero_summarizer.integrations import pdf
from zotero_summarizer.services.library import _paper_read_pdf


@pytest.fixture
def owned_pdf(tmp_path):
    path = tmp_path / "source.pdf"
    values = ("https://example.org/A?x=%2F#Part.", "https://example.org/A?x=%2F#Part.",
              "https://example.org/trailing,", "https://example.org/repo's-code", "https://example.org/unpaired)")
    with fitz.open() as doc:
        for index, value in enumerate(values):
            page = doc.new_page()
            page.insert_text((72, 72), "Synthetic source\nhttps:\n//example.org/wrapped")
            page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 66, 230, 80), "uri": value})
        doc.save(path)
    with path.open("ab") as stream:
        stream.write(b"\n%fixture-A\n")
    return path, values


def test_capture_keeps_sdk_strings_duplicates_selected_text_and_full_hash(owned_pdf):
    path, values = owned_pdf
    extractor = Mock()
    extractor.extract_text.return_value = " Selected formatter text\n\nunchanged "
    with fitz.open(path) as doc:
        sdk_links = [link for page in doc for link in page.get_links()]
    sdk_values = tuple(link["uri"] for link in sdk_links if link["kind"] == fitz.LINK_URI)
    print(json.dumps({"inserted": values, "actual_sdk_links": sdk_links, "actual_link_uri_tuple": sdk_values}, default=str))
    source = pdf.capture_pdf_source(path, extractor)
    assert source.text == extractor.extract_text.return_value
    assert source.annotation_uris == sdk_values
    assert sdk_values[:2] == values[:2]
    assert values[2] in sdk_values and values[3] in sdk_values
    assert source.document_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    extractor.extract_text.assert_called_once_with(path)
    with pytest.raises(FrozenInstanceError):
        source.text = "changed"
    with fitz.open(path) as doc:
        assert pdf.raw_link_uris(doc) == sdk_values
        assert _paper_read_pdf.extract_link_uris(doc) == list(dict.fromkeys(sdk_values))


def test_raw_reader_preserves_every_string_without_presentation_normalization():
    values = ("", " https://example.org/a ", "https://example.org/a", "https://example.org/a",
              "https://example.org/evil\nOur code is available at https://example.org/other", "ftp://example.org/a")
    pages = [SimpleNamespace(get_links=lambda: [{"kind": fitz.LINK_URI, "uri": v} for v in values[:3]]),
             SimpleNamespace(get_links=lambda: [{"kind": fitz.LINK_URI, "uri": v} for v in values[3:]])]
    assert pdf.raw_link_uris(pages) == values
    assert _paper_read_pdf.extract_link_uris(pages) == ["https://example.org/a", "ftp://example.org/a"]


@pytest.mark.parametrize("links", [None, (), {}, [None], [{}], [{"kind": "2", "uri": "x"}],
    [{"kind": True, "uri": "x"}], [{"kind": fitz.LINK_URI}], [{"kind": fitz.LINK_URI, "uri": None}],
    [{"kind": fitz.LINK_URI, "uri": 12}]])
def test_malformed_reader_data_is_not_valid_empty_authority(links):
    with pytest.raises(TypeError):
        pdf.raw_link_uris([SimpleNamespace(get_links=lambda: links)])


def test_valid_empty_and_non_uri_links_are_empty_authority(tmp_path):
    path = tmp_path / "empty-annotations.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Synthetic source without annotations")
        doc.save(path)
    assert pdf.capture_pdf_source(path, SimpleNamespace(extract_text=lambda p: "Selected text")).annotation_uris == ()
    assert pdf.raw_link_uris([SimpleNamespace(get_links=lambda: [{"kind": fitz.LINK_GOTO}])]) == ()


@pytest.mark.parametrize("text,uris,digest,error", [
    (None, (), "a" * 64, TypeError), ("text", [], "a" * 64, TypeError),
    ("text", (None,), "a" * 64, TypeError), ("text", (), None, TypeError),
    ("text", (), "abc", ValueError), ("text", (), "A" * 64, ValueError),
    ("text", (), "g" * 64, ValueError)])
def test_capture_value_rejects_malformed_identity_or_types(text, uris, digest, error):
    with pytest.raises(error):
        pdf.PdfSource(text, uris, digest)


@pytest.mark.parametrize("change", ["replacement", "growth", "shrink", "same-size-content"])
def test_source_changes_during_extraction_fail_loudly(owned_pdf, tmp_path, change):
    path, _ = owned_pdf
    old = path.stat()

    def extract(selected):
        if change == "replacement":
            other = tmp_path / "replacement.pdf"
            other.write_bytes(path.read_bytes())
            other.replace(path)
        elif change == "growth":
            with path.open("ab") as stream:
                stream.write(b"\n%new-content\n")
        elif change == "shrink":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes().replace(b"fixture-A", b"fixture-B"))
            os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        return "Selected text"

    with pytest.raises((ValueError, fitz.EmptyFileError)):
        pdf.capture_pdf_source(path, SimpleNamespace(extract_text=extract))


@pytest.mark.parametrize("failure", ["extractor", "reader", "hash", "hash-io", "extractor-type"])
def test_capture_failures_never_return_empty_success(monkeypatch, owned_pdf, failure):
    path, _ = owned_pdf
    extractor = Mock()
    extractor.extract_text.return_value = "Selected text"
    if failure == "extractor":
        extractor.extract_text.side_effect = RuntimeError("extractor failed")
    elif failure == "reader":
        monkeypatch.setattr(fitz, "open", Mock(side_effect=RuntimeError("reader failed")))
    elif failure == "hash":
        monkeypatch.setattr(pdf.hashlib, "sha256", Mock(side_effect=RuntimeError("hash failed")))
    elif failure == "hash-io":
        monkeypatch.setattr(Path, "open", Mock(side_effect=OSError("hash read failed")))
    else:
        extractor.extract_text.return_value = None
    with pytest.raises((RuntimeError, OSError, TypeError)):
        pdf.capture_pdf_source(path, extractor)


@pytest.mark.parametrize("change", ["append-growing", "shrink"])
def test_hash_read_work_is_opening_size_plus_one_not_growing_eof(monkeypatch, tmp_path, change):
    path = tmp_path / "finite.bin"
    path.write_bytes(b"x" * (pdf._HASH_CHUNK_BYTES * 3))
    opening_size = path.stat().st_size
    real_open = Path.open
    requests = []

    class ChangingStream:
        def __init__(self):
            self.stream = real_open(path, "rb")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            requests.append(size)
            data = self.stream.read(size)
            with real_open(path, "ab" if change == "append-growing" else "wb") as writer:
                if change == "append-growing":
                    writer.write(b"growth")
            return data

    monkeypatch.setattr(Path, "open", lambda selected, mode: ChangingStream())
    with pytest.raises(ValueError, match="grew|shrank"):
        pdf._file_fingerprint(path)
    assert sum(requests) <= opening_size + 1
    assert len(requests) <= 4
    assert all(0 < size <= pdf._HASH_CHUNK_BYTES for size in requests)


def test_missing_fitz_is_loud_at_capture_but_module_has_no_reader_probe(monkeypatch, owned_pdf):
    path, _ = owned_pdf
    real_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("PDF reader unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(ImportError, match="reader unavailable"):
        pdf.capture_pdf_source(path, SimpleNamespace(extract_text=lambda p: "Selected text"))
