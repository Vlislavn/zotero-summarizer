"""Tests for the Stage-2 library reading queue: gate-relevance ranking, live
read-status filter, incremental cache, and graceful gate-off fallback."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.services.library import reading_queue


class _FakeReader:
    def __init__(self, items):
        self._items = items

    def get_items(self, *, limit=100, offset=0, collection_key=None, search=None, tag=None):
        return {"items": self._items, "total": len(self._items)}


class _Pred:
    def __init__(self, item_key, raw_score, shap=None, aux=None):
        self.item_key = item_key
        self.raw_score = raw_score
        self.shap_contribs = shap or []
        self.aux_context = aux or {}


class _FakeGate:
    def __init__(self, sha, scores=None):
        self.golden_csv_sha256 = sha
        self._scores = scores or {}

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False):
        return [
            _Pred(
                it["item_key"], self._scores.get(it["item_key"], 3.0),
                shap=[
                    {"feature": "semantic_match_specter2", "contribution": 0.5},
                    {"feature": "bias", "contribution": 2.0},
                ],
                aux={"max_author_h_index": 20},
            )
            for it in items
        ]


def _item(key, pri="", date="2026-05-01", tags=()):
    return {
        "item_key": key, "title": f"T{key}", "abstract": "abs", "authors": "A",
        "reading_priority": pri, "has_pdf": True, "date_added": date, "tags": list(tags),
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No real cache file, no background threads, clean job state per test.
    Default: no user verdicts (handled-filter off) unless a test overrides it."""
    from zotero_summarizer.storage import repositories

    reading_queue.finish(error=None)
    monkeypatch.setattr(reading_queue, "_cache_path", lambda: tmp_path / "rq.json")
    monkeypatch.setattr(reading_queue, "run_in_background", lambda target: None)
    monkeypatch.setattr(
        reading_queue, "get_settings",
        lambda: SimpleNamespace(corpus_db_path=tmp_path / "c.db", triage_db_path=tmp_path / "t.db"),
    )
    monkeypatch.setattr(repositories, "list_label_verdicts", lambda db_path, **k: [])
    yield
    reading_queue.finish(error=None)


def _patch_state(monkeypatch, reader, gate):
    monkeypatch.setattr(
        reading_queue, "get_state",
        lambda: SimpleNamespace(zotero_reader=reader, classifier_gate=gate, app_state=SimpleNamespace(config=object())),
    )


def _seed(sha, **scores):
    reading_queue._write_cache(sha, {
        k: {"relevance_score": v, "why_reason": "Topic match", "scoring": {"composite_score": v, "shap_top": []}}
        for k, v in scores.items()
    })


def test_ranks_by_relevance_when_cached(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("B"), _item("C")]), _FakeGate("sha1"))
    _seed("sha1", A=2.0, B=4.0, C=3.0)
    res = reading_queue.build_reading_queue()
    assert [i["item_key"] for i in res["items"]] == ["B", "C", "A"]
    assert res["status"] == "ready"
    assert res["model_ready"] is True
    assert res["items"][0]["relevance_score"] == 4.0


def test_open_does_not_autocompute_when_scores_missing(monkeypatch):
    """Opening NEVER rescans, even with no cached scores — that's the fix for
    'scoring re-runs slowly on open'. The item shows unscored; Rescore computes."""
    _patch_state(monkeypatch, _FakeReader([_item("A")]), _FakeGate("sha1"))
    res = reading_queue.build_reading_queue()  # nothing seeded
    assert res["status"] == "ready"
    assert reading_queue.is_running() is False
    assert res["items"][0]["relevance_score"] is None


def test_stale_cache_scores_returned_with_flag(monkeypatch):
    """After a gate retrain the cache sha mismatches, but scores must NOT be
    wiped (no forced rescore on open) — they're returned with scores_stale."""
    _patch_state(monkeypatch, _FakeReader([_item("A")]), _FakeGate("new-sha"))
    _seed("old-sha", A=3.5)
    res = reading_queue.build_reading_queue()
    assert res["status"] == "ready"
    assert reading_queue.is_running() is False
    assert res["items"][0]["relevance_score"] == 3.5
    assert res["scores_stale"] is True


def test_filters_passed_through_to_reader(monkeypatch):
    """collection/tag/search scope the displayed rows via the reader's own
    filtering (the merged Browse capability)."""
    captured = {}

    class _CapturingReader(_FakeReader):
        def get_items(self, *, limit=100, offset=0, collection_key=None, search=None, tag=None):
            captured.update(collection_key=collection_key, tag=tag, search=search)
            return super().get_items(limit=limit, offset=offset)

    _patch_state(monkeypatch, _CapturingReader([_item("A")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    reading_queue.build_reading_queue(collection="COLL", tag="🧪 method", search="brain")
    assert captured == {"collection_key": "COLL", "tag": "🧪 method", "search": "brain"}


def test_excludes_items_with_a_user_verdict(monkeypatch):
    """A paper the user has verdicted (esp. dont_read) is 'handled' and must not
    appear in Read next — even though it has no engagement emoji."""
    from zotero_summarizer.storage import repositories

    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("V")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0, V=4.0)
    monkeypatch.setattr(
        repositories, "list_label_verdicts",
        lambda db_path, **k: [{"item_key": "V", "user_priority": "dont_read"}],
    )
    res = reading_queue.build_reading_queue()
    keys = [i["item_key"] for i in res["items"]]
    assert "V" not in keys and "A" in keys
    assert res["read_hidden"] == 1  # V counted as handled/hidden


def test_read_items_hidden_live_and_shown_with_toggle(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("R", tags=["🧠"])]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    hidden = reading_queue.build_reading_queue(include_read=False)
    assert [i["item_key"] for i in hidden["items"]] == ["A"]
    assert hidden["read_hidden"] == 1
    shown = reading_queue.build_reading_queue(include_read=True)
    assert "R" in [i["item_key"] for i in shown["items"]]


def test_gate_off_falls_back_to_priority_then_recency(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A", "could_read"), _item("B", "must_read")]), None)
    res = reading_queue.build_reading_queue()
    assert res["model_ready"] is False
    assert res["status"] == "ready"
    assert [i["item_key"] for i in res["items"]] == ["B", "A"]


def test_scoring_from_prediction_maps_value_and_composite():
    pred = _Pred(
        "X", 3.7,
        shap=[{"feature": "semantic_match_specter2", "contribution": 0.6}, {"feature": "bias", "contribution": 2.4}],
        aux={"max_author_h_index": 20},
    )
    sc = reading_queue.scoring_from_prediction(pred)
    assert sc["composite_score"] == 3.7
    assert {"feature": "semantic_match_specter2", "value": 0.6} in sc["shap_top"]
    assert sc["prestige_score"] is not None


def test_why_reason_excludes_baseline():
    sc = {"shap_top": [{"feature": "bias", "value": 2.4}, {"feature": "semantic_match_specter2", "value": 0.6}]}
    assert reading_queue._why_reason(sc) == "Topic match"


def test_live_scoring_single_item_and_no_abstract(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([]), _FakeGate("sha1", {"X": 4.2}))
    sc = reading_queue.live_scoring({"item_key": "X", "title": "t", "abstract": "a"})
    assert sc["composite_score"] == 4.2
    assert reading_queue.live_scoring({"item_key": "Y", "title": "t", "abstract": ""}) is None


def test_get_cached_scoring_roundtrip(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([]), _FakeGate("sha1"))
    _seed("sha1", A=3.3)
    assert reading_queue.get_cached_scoring("A")["composite_score"] == 3.3
    assert reading_queue.get_cached_scoring("missing") is None


class _PartialGate(_FakeGate):
    """Predicts everything except keys in ``skip`` — simulates a ``pred is None``
    item (gate returns no row for it, e.g. no usable embedding)."""

    def __init__(self, sha, skip):
        super().__init__(sha)
        self._skip = set(skip)

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False):
        scorable = [it for it in items if it["item_key"] not in self._skip]
        return super().predict(scorable, corpus_db_path=corpus_db_path, goals_config=goals_config, return_shap=return_shap)


def test_unscorable_item_gets_sentinel_and_stops_recompute(monkeypatch):
    """The core fix: an item the gate can't score is cached as a sentinel so it
    no longer counts as 'missing' and never re-triggers the background pass."""
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("U")]), _PartialGate("sha1", skip={"U"}))
    reading_queue._compute_scores_into_cache("sha1")
    cached = reading_queue._read_cache("sha1")
    assert cached["A"]["relevance_score"] is not None
    assert cached["U"].get("unscorable") is True
    assert cached["U"]["relevance_score"] is None
    res = reading_queue.build_reading_queue()
    assert res["status"] == "ready"  # U is attempted → not missing → no loop
    assert [i["item_key"] for i in res["items"]][0] == "A"
    u = next(i for i in res["items"] if i["item_key"] == "U")
    assert u["relevance_score"] is None


def test_job_error_surfaced_and_not_auto_retried(monkeypatch):
    """A crashed background job is reported (status 'error') and NOT auto-retried —
    the user retries via Rescore, so it can't crash-loop on every reload."""
    _patch_state(monkeypatch, _FakeReader([_item("A")]), _FakeGate("sha1"))
    reading_queue.finish(error="RuntimeError: boom")
    res = reading_queue.build_reading_queue()
    assert res["status"] == "error"
    assert "boom" in res["error"]
    assert reading_queue.is_running() is False  # did not relaunch


def test_refresh_recomputes_despite_cache_and_error(monkeypatch):
    """The Rescore button (refresh=True) forces a recompute even when everything
    is cached and a prior error is set; the stale error isn't surfaced mid-run."""
    _patch_state(monkeypatch, _FakeReader([_item("A")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    reading_queue.finish(error="boom")
    res = reading_queue.build_reading_queue(refresh=True)
    assert res["status"] == "computing"
    assert res["error"] is None


def test_build_reading_queue_reports_computed_at(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    res = reading_queue.build_reading_queue()
    assert res["status"] == "ready"
    assert res["computed_at"]  # ISO string from the cache file
