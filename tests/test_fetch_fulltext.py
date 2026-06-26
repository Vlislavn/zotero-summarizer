"""services/library/fulltext — fetch arXiv PDFs and attach to Zotero.

Stubs the network fetch + the writer so the classification (skip-has-pdf /
no-arxiv / fetch-fail) and the add_attachment change shape are tested without
touching the network or a real Zotero DB."""
from __future__ import annotations


from zotero_summarizer.services.library import fulltext


class _FakeWriter:
    def __init__(self, running: bool = False):
        self._running = running
        self.calls: list = []

    def is_connector_running(self) -> bool:
        return self._running

    def apply_changes(self, changes, create_backup):
        self.calls.append((changes, create_backup))
        return {"applied_ids": list(range(len(changes))), "failed": [], "backup_path": "/tmp/zotero.bak"}


def _arxiv(key: str) -> dict:
    return {"item_key": key, "has_pdf": False, "url": "http://arxiv.org/abs/1706.03762", "doi": ""}


def test_attaches_arxiv_pdf_with_backup_first(monkeypatch, tmp_path):
    pdf = tmp_path / "1706.03762.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(fulltext.pdf_fetch, "fetch_pdf", lambda url, **k: pdf)
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    res = fulltext.fetch_fulltext_for_items([_arxiv("A")])

    assert res["attached"] == 1 and res["backup_path"] == "/tmp/zotero.bak"
    changes, create_backup = writer.calls[0]
    assert create_backup is True                              # backup-first
    c = changes[0]
    assert c["change_type"] == "add_attachment" and c["item_key"] == "A"
    assert c["payload_json"]["source_path"] == str(pdf)
    assert c["payload_json"]["source_url"].endswith("1706.03762.pdf")  # arXiv PDF url
    assert c["payload_json"]["filename"] == "1706.03762.pdf"


def test_skips_items_that_already_have_a_pdf(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(fulltext.pdf_fetch, "fetch_pdf", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    res = fulltext.fetch_fulltext_for_items([{**_arxiv("A"), "has_pdf": True}])

    assert res["skipped_has_pdf"] == 1 and res["attached"] == 0
    assert called["n"] == 0 and writer.calls == []            # no fetch, no write


def test_skips_items_without_an_arxiv_link(monkeypatch):
    monkeypatch.setattr(fulltext.pdf_fetch, "fetch_pdf", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")))
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    res = fulltext.fetch_fulltext_for_items(
        [{"item_key": "A", "has_pdf": False, "url": "https://example.com/paper", "doi": "10.1/x"}]
    )

    assert res["no_arxiv"] == 1 and res["attached"] == 0 and writer.calls == []


def test_resolves_ar5iv_and_arxiv_html_urls(monkeypatch, tmp_path):
    # ar5iv / arxiv.org/html URLs embed the id but dodge the abs|pdf matcher —
    # they must still resolve to the arXiv PDF (real-data gap found in review).
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(fulltext.pdf_fetch, "fetch_pdf", lambda url, **k: pdf)
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)
    items = [
        {"item_key": "AR5IV", "has_pdf": False, "url": "https://ar5iv.labs.arxiv.org/html/2410.17309", "doi": ""},
        {"item_key": "HTML", "has_pdf": False, "url": "https://arxiv.org/html/2401.00001v2", "doi": ""},
    ]
    res = fulltext.fetch_fulltext_for_items(items)
    assert res["attached"] == 2 and res["no_arxiv"] == 0
    urls = {c["item_key"]: c["payload_json"]["source_url"] for c in writer.calls[0][0]}
    assert urls["AR5IV"].endswith("2410.17309.pdf")
    assert "2401.00001" in urls["HTML"]


def test_arxiv_only_does_not_grab_random_pdf_urls():
    # The arXiv-only contract: a non-arXiv .pdf URL must NOT resolve (we never
    # attach a random publisher PDF — the goal is arXiv full text).
    assert fulltext._arxiv_pdf_url("https://ar5iv.labs.arxiv.org/html/2410.17309", "").endswith("2410.17309.pdf")
    assert fulltext._arxiv_pdf_url("https://arxiv.org/abs/1706.03762", "").endswith("1706.03762.pdf")
    assert fulltext._arxiv_pdf_url("https://journal.example.com/paper.pdf", "") is None


def test_fetch_failure_recorded_not_fatal(monkeypatch):
    monkeypatch.setattr(fulltext.pdf_fetch, "fetch_pdf", lambda url, **k: None)  # arXiv 404/non-PDF
    writer = _FakeWriter()
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    res = fulltext.fetch_fulltext_for_items([_arxiv("A"), _arxiv("B")])

    assert res["attached"] == 0 and res["failed_count"] == 2 and writer.calls == []


def test_requires_force_when_zotero_running(monkeypatch):
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: _FakeWriter(running=True))
    res = fulltext.fetch_fulltext_for_items([_arxiv("A")], force=False)
    assert res.get("requires_force") is True
