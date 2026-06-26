"""Abstract backfill: recover title-only RSS items from OpenAlex before the gate.

The classifier gate drops items without a title+abstract; many RSS entries arrive
title-only. ``_backfill_abstracts`` fills the gap from OpenAlex (already queried
for prestige) so those items become scorable instead of being silently lost.
"""
from __future__ import annotations

from zotero_summarizer.services.model.classifier_artifact import _backfill_abstracts


class _FakeWork:
    def __init__(self, abstract: str | None) -> None:
        self.abstract = abstract


class _FakeClient:
    """Resolves abstracts from canned doi/title maps and records the lookups."""

    def __init__(self, by_doi: dict | None = None, by_title: dict | None = None) -> None:
        self.by_doi = by_doi or {}
        self.by_title = by_title or {}
        self.doi_calls: list[str] = []
        self.title_calls: list[tuple[str, int | None]] = []

    def fetch_work_by_doi(self, doi: str):
        self.doi_calls.append(doi)
        a = self.by_doi.get(doi)
        return _FakeWork(a) if a is not None else None

    def fetch_work_by_title(self, title: str, *, year: int | None = None):
        self.title_calls.append((title, year))
        a = self.by_title.get(title)
        return _FakeWork(a) if a is not None else None


def test_backfill_by_doi():
    items = [{"title": "T", "abstract": "", "doi": "10.1/x"}]
    client = _FakeClient(by_doi={"10.1/x": "recovered abstract"})
    assert _backfill_abstracts(items, client) == 1
    assert items[0]["abstract"] == "recovered abstract"
    assert client.title_calls == []  # a DOI hit short-circuits the title fallback


def test_backfill_title_fallback_when_no_doi():
    items = [{"title": "A long enough paper title", "abstract": "", "doi": "",
              "publication_date": "2024-05-01"}]
    client = _FakeClient(by_title={"A long enough paper title": "from title"})
    assert _backfill_abstracts(items, client) == 1
    assert items[0]["abstract"] == "from title"
    assert client.title_calls == [("A long enough paper title", 2024)]


def test_backfill_noop_when_already_has_abstract():
    items = [{"title": "T", "abstract": "already here", "doi": "10.1/x"}]
    client = _FakeClient(by_doi={"10.1/x": "should not be used"})
    assert _backfill_abstracts(items, client) == 0
    assert items[0]["abstract"] == "already here"
    assert client.doi_calls == []  # not even looked up


def test_backfill_leaves_unresolvable_empty():
    items = [{"title": "T", "abstract": "", "doi": "10.1/missing"}]
    client = _FakeClient()  # resolves nothing
    assert _backfill_abstracts(items, client) == 0
    assert items[0]["abstract"] == ""
