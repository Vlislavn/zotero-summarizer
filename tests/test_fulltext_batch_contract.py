"""Attachment batches deduplicate before acquisition and fail before any write."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_fetch_fulltext import _FakeWriter, _item, _acquired
from zotero_summarizer.services.library import fulltext


@pytest.mark.parametrize("existing", [(False, False), (False, True), (True, False)])
def test_duplicate_items_create_one_outcome_and_at_most_one_attachment(monkeypatch, tmp_path, existing):
    writer, seen = _FakeWriter(), []
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)
    def acquire(key, *args, **kwargs):
        seen.append(key)
        return _acquired(tmp_path, "arxiv")
    monkeypatch.setattr(fulltext._pdf_acquire, "acquire_pdf_for", acquire)
    items = [_item("A", has_pdf=existing[0]), _item("B"), _item("A", has_pdf=existing[1])]
    original = deepcopy(items)

    result = fulltext.fetch_fulltext_for_items(items)

    expected = ["B"] if any(existing) else ["A", "B"]
    assert seen == expected
    assert [change["item_key"] for change in writer.calls[0][0]] == expected
    assert writer.calls[0][1] is True
    assert sorted(row["item_key"] for row in result["outcomes"]) == ["A", "B"]
    assert result["attached"] == len(expected)
    assert result["skipped_has_pdf"] == int(any(existing))
    assert fulltext.progress() == {"done": len(expected), "total": len(expected)}
    assert items == original


def test_acquisition_error_aborts_before_all_zotero_writes(monkeypatch, tmp_path):
    writer, seen = _FakeWriter(), []
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)
    def acquire(key, *args, **kwargs):
        seen.append(key)
        if key == "B":
            raise RuntimeError("resolver failed")
        return _acquired(tmp_path, "arxiv")
    monkeypatch.setattr(fulltext._pdf_acquire, "acquire_pdf_for", acquire)

    with pytest.raises(RuntimeError, match="resolver failed"):
        fulltext.fetch_fulltext_for_items([_item("A"), _item("B"), _item("C")])

    assert seen == ["A", "B"]
    assert writer.calls == []


def test_background_error_is_reported_and_reraised(monkeypatch):
    jobs = []
    monkeypatch.setattr(fulltext, "_RUNNING", False)
    monkeypatch.setattr(fulltext, "_RESULT", None)
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: _FakeWriter())
    monkeypatch.setattr(fulltext.threading, "Thread", lambda *, target, **kw: SimpleNamespace(start=lambda: jobs.append(target)))
    def fail(**kw):
        raise RuntimeError("resolver failed")
    monkeypatch.setattr(fulltext, "fetch_fulltext_bulk", fail)

    assert fulltext.start_bulk() == {"status": "started"}
    with pytest.raises(RuntimeError, match="resolver failed"):
        jobs[0]()

    state = fulltext.status()
    assert state["running"] is False
    assert state["result"] == {"error": "RuntimeError: resolver failed"}
