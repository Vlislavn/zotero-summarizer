"""build_library_detail now fills `scoring` (cache → live → None) so the
annotation detail shows 'Why this score?' for library items."""
from __future__ import annotations

from zotero_summarizer.services.library import review_detail, reading_queue


class _Reader:
    def __init__(self, detail):
        self._d = detail

    def get_item_detail(self, key):
        return self._d


_DETAIL = {"title": "T", "abstract": "a", "authors": [], "publication_title": "V"}
_SCORING = {"composite_score": 3.5, "prestige_score": None, "shap_top": [], "prestige_inputs": None}


def test_uses_cached_scoring_when_present(monkeypatch):
    monkeypatch.setattr(reading_queue, "get_cached_scoring", lambda k: dict(_SCORING, composite_score=3.5))
    d = review_detail.build_library_detail(_Reader(_DETAIL), "ABCD1234")
    assert d["scoring"]["composite_score"] == 3.5


def test_live_scores_when_no_cache(monkeypatch):
    monkeypatch.setattr(reading_queue, "get_cached_scoring", lambda k: None)
    monkeypatch.setattr(reading_queue, "live_scoring", lambda item: dict(_SCORING, composite_score=4.1))
    d = review_detail.build_library_detail(_Reader(_DETAIL), "ABCD1234")
    assert d["scoring"]["composite_score"] == 4.1


def test_scoring_none_when_gate_off(monkeypatch):
    monkeypatch.setattr(reading_queue, "get_cached_scoring", lambda k: None)
    monkeypatch.setattr(reading_queue, "live_scoring", lambda item: None)
    d = review_detail.build_library_detail(_Reader(_DETAIL), "ABCD1234")
    assert d["scoring"] is None


def test_returns_none_when_item_missing(monkeypatch):
    monkeypatch.setattr(reading_queue, "get_cached_scoring", lambda k: None)
    d = review_detail.build_library_detail(_Reader(None), "ABCD1234")
    assert d is None
