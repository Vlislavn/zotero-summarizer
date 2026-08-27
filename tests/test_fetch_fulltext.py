"""Multi-source PDF acquisition → backup-first Zotero attachment."""
from pathlib import Path

import pytest

from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.services.library import _pdf_acquire, fulltext


class _FakeWriter:
    def __init__(self, *, running=False, fail=False):
        self.running, self.fail, self.calls = running, fail, []

    def is_connector_running(self):
        return self.running

    def apply_changes(self, changes, create_backup):
        self.calls.append((changes, create_backup))
        applied = [] if self.fail else [change["id"] for change in changes]
        return {"applied_ids": applied, "failed": [{}] if self.fail else [],
                "backup_path": "/tmp/zotero.bak"}


def _item(key="A", **extra):
    return {"item_key": key, "has_pdf": False, "url": "", "doi": "", **extra}


def _acquired(tmp_path: Path, source: str):
    path = tmp_path / f"{source}.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    return _pdf_acquire.AcquireResult(
        path=path, source=source, source_url=f"https://oa.test/{source}.pdf",
        outcome=f"acquired_{source}",
    )


@pytest.mark.parametrize("source", ["arxiv", "unpaywall", "pmc", "openalex", "direct"])
def test_attaches_every_oa_source_with_typed_outcome(monkeypatch, tmp_path, source):
    acquired = _acquired(tmp_path, source)
    monkeypatch.setattr(fulltext._pdf_acquire, "acquire_pdf_for", lambda *a, **k: acquired)
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    result = fulltext.fetch_fulltext_for_items([_item()])

    assert result["outcomes"][0]["status"] == f"attached_{source}"
    assert result["attached"] == 1 and result["backup_path"] == "/tmp/zotero.bak"
    changes, backup = writer.calls[0]
    assert backup is True and changes[0]["change_type"] == "add_attachment"
    assert changes[0]["payload_json"]["source_url"] == acquired.source_url


def test_cached_deep_review_pdf_is_reused_without_acquisition(monkeypatch, tmp_path):
    cached = _acquired(tmp_path, "unpaywall")
    monkeypatch.setattr(
        fulltext._pdf_acquire, "acquire_pdf_for",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not refetch")),
    )
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: _FakeWriter())
    item = _item(cached_acquisition={
        "path": str(cached.path), "source": cached.source, "source_url": cached.source_url,
    })

    result = fulltext.fetch_fulltext_for_items([item])

    assert result["outcomes"][0] == {
        "item_key": "A", "path": str(cached.path), "source": "unpaywall",
        "source_url": cached.source_url, "status": "attached_cached",
    }


def test_skip_unavailable_and_write_failure_are_distinct(monkeypatch):
    results = {
        "NO": _pdf_acquire.AcquireResult(path=None, outcome="no_oa_source"),
        "FETCH": _pdf_acquire.AcquireResult(path=None, outcome="fetch_failed"),
    }
    monkeypatch.setattr(fulltext._pdf_acquire, "acquire_pdf_for", lambda key, *_a, **_k: results[key])
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    result = fulltext.fetch_fulltext_for_items([
        _item("HAVE", has_pdf=True), _item("NO"), _item("FETCH"),
    ])

    assert [row["status"] for row in result["outcomes"]] == [
        "skipped_has_pdf", "no_oa_source", "fetch_failed",
    ]
    assert result["skipped_has_pdf"] == result["no_oa_source"] == result["failed_count"] == 1
    assert writer.calls == []


def test_write_failure_is_per_item(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fulltext._pdf_acquire, "acquire_pdf_for", lambda *a, **k: _acquired(tmp_path, "arxiv")
    )
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: _FakeWriter(fail=True))
    result = fulltext.fetch_fulltext_for_items([_item()])
    assert result["outcomes"][0]["status"] == "write_failed" and result["failed_count"] == 1


def test_bulk_delegates_to_same_engine(monkeypatch):
    reader = type("Reader", (), {
        "get_all_items": lambda self, **_k: {"items": [{"item_key": "A", "has_pdf": False}]},
        "get_field_values": lambda self, field: {"A": "10.1/x" if field == "DOI" else ""},
    })()
    seen = {}
    monkeypatch.setattr(fulltext, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(
        fulltext, "fetch_fulltext_for_items",
        lambda items, **kw: seen.update(items=items, kw=kw) or {"attached": 1},
    )
    assert fulltext.fetch_fulltext_bulk()["attached"] == 1
    assert seen["items"][0]["doi"] == "10.1/x"


def test_connector_guard_and_arxiv_variants(monkeypatch):
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: _FakeWriter(running=True))
    assert fulltext.fetch_fulltext_for_items([_item()]).get("requires_force") is True
    assert _arxiv_id_from_url_or_doi("https://ar5iv.labs.arxiv.org/html/2410.17309", "") == "2410.17309"
    assert _arxiv_id_from_url_or_doi("https://arxiv.org/html/2401.00001v2", "") == "2401.00001v2"
