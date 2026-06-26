"""Tests for the Stage-2 library reading queue: gate-relevance ranking, live
read-status filter, incremental cache, and graceful gate-off fallback."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.services.library import reading_queue


class _FakeReader:
    def __init__(self, items):
        self._items = items

    def get_items(self, *, limit=100, offset=0, collection_key=None, search=None, tag=None, include_abstract=True):
        # The whole-library queue + scoring must read via get_all_items now; a
        # reversion to the 500-window get_items should fail loudly here.
        raise AssertionError("reading_queue must use get_all_items, not get_items")

    def get_all_items(self, *, collection_key=None, search=None, tag=None, page_size=500, include_abstract=True):
        # Whole-library scan source (the real one paginates get_items); the fake
        # already returns everything in one page.
        return {"items": self._items, "total": len(self._items)}


class _Pred:
    def __init__(self, item_key, raw_score, shap=None, aux=None):
        self.item_key = item_key
        self.raw_score = raw_score
        self.shap_contribs = shap or []
        self.aux_context = aux or {}


def test_score_distribution_bins_by_band():
    records = [
        {"relevance_score": 4.8},   # must_read  (bin 4.5–5.0)
        {"relevance_score": 4.0},   # should_read(bin 4.0–4.5)
        {"relevance_score": 3.6},   # should_read(bin 3.5–4.0)
        {"relevance_score": 2.2},   # could_read (bin 2.0–2.5)
        {"relevance_score": 1.2},   # dont_read  (bin 1.0–1.5)
        {"relevance_score": None},  # unscored
    ]
    dist = reading_queue._score_distribution(records)
    assert dist["total_scored"] == 5
    assert dist["unscored"] == 1
    assert dist["by_band"] == {"must_read": 1, "should_read": 2, "could_read": 1, "dont_read": 1}
    assert len(dist["bins"]) == 8
    assert dist["bins"][-1]["band"] == "must_read" and dist["bins"][-1]["count"] == 1  # 4.5–5.0
    assert dist["bins"][0]["band"] == "dont_read" and dist["bins"][0]["count"] == 1    # 1.0–1.5
    assert sum(b["count"] for b in dist["bins"]) == 5


class _FakeGate:
    def __init__(self, sha, scores=None):
        self.golden_csv_sha256 = sha
        self._scores = scores or {}

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False, prestige_network=True):
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
    monkeypatch.setattr(repositories, "list_label_verdict_priorities", lambda db_path: {})
    # No review-fleet proposals by default (hermetic — never reads the real model
    # dir's proposed_verdicts.json); tests that exercise the attach override this.
    from zotero_summarizer.services.library.review_fleet import verdict_store
    monkeypatch.setattr(verdict_store, "read_all", lambda: {})
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
        def get_all_items(self, *, collection_key=None, search=None, tag=None, page_size=500, include_abstract=True):
            captured.update(collection_key=collection_key, tag=tag, search=search)
            return super().get_all_items()

    _patch_state(monkeypatch, _CapturingReader([_item("A")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    reading_queue.build_reading_queue(collection="COLL", tag="🧪 method", search="brain")
    assert captured == {"collection_key": "COLL", "tag": "🧪 method", "search": "brain"}


def test_dont_read_verdict_hides_paper(monkeypatch):
    """A ``dont_read`` verdict is 'handled' and must not appear in Read next —
    even though it has no engagement emoji (a rejected paper must not reappear)."""
    from zotero_summarizer.storage import repositories

    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("V")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0, V=4.0)
    monkeypatch.setattr(
        repositories, "list_label_verdict_priorities",
        lambda db_path: {"V": "dont_read"},
    )
    res = reading_queue.build_reading_queue()
    keys = [i["item_key"] for i in res["items"]]
    assert "V" not in keys and "A" in keys
    assert res["read_hidden"] == 1  # V counted as handled/hidden


def test_proposed_verdict_attached_to_rows(monkeypatch):
    """The review-fleet's pre-decided verdict is attached to each row as
    ``proposed_verdict`` (a SUGGESTION the user Confirms/Overrides); rows without
    a proposal carry ``None``."""
    from zotero_summarizer.services.library.review_fleet import verdict_store

    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("B")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0, B=4.0)
    monkeypatch.setattr(
        verdict_store, "read_all",
        lambda: {"A": {"proposed": "must_read", "confidence": 0.85}},
    )
    res = reading_queue.build_reading_queue()
    by_key = {i["item_key"]: i for i in res["items"]}
    assert by_key["A"]["proposed_verdict"] == {"proposed": "must_read", "confidence": 0.85}
    assert by_key["B"]["proposed_verdict"] is None  # no proposal yet


def test_proposed_dont_read_does_not_hide_paper(monkeypatch):
    """A ``dont_read`` SUGGESTION must NOT auto-hide a paper — only the user's
    CONFIRMED ``dont_read`` label (via _verdict_priorities) does. The proposal is
    display-only and is never routed through the handled/hide logic."""
    from zotero_summarizer.storage import repositories
    from zotero_summarizer.services.library.review_fleet import verdict_store

    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("S")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0, S=4.0)
    monkeypatch.setattr(repositories, "list_label_verdict_priorities", lambda db_path: {})
    monkeypatch.setattr(
        verdict_store, "read_all",
        lambda: {"S": {"proposed": "dont_read", "confidence": 0.75}},
    )
    res = reading_queue.build_reading_queue()
    keys = [i["item_key"] for i in res["items"]]
    assert "S" in keys  # the dont_read SUGGESTION did not hide it
    assert res["read_hidden"] == 0
    proposed = next(i for i in res["items"] if i["item_key"] == "S")["proposed_verdict"]
    assert proposed["proposed"] == "dont_read"


def test_positive_verdict_stays_visible_and_pins_to_top(monkeypatch):
    """REGRESSION: a positive verdict (must/should/could_read) is a reading
    INTENT, not 'done' — the paper must stay in Read next AND pin to the top,
    even with a lower relevance score than the rest. Previously ANY verdict
    marked a paper 'handled', so labelling a paper made it vanish from the queue
    (unfindable). Only ``dont_read`` hides; positive labels surface."""
    from zotero_summarizer.storage import repositories

    # L has the LOWEST relevance (2.0) but a must_read label → must lead the list.
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("B"), _item("L")]), _FakeGate("sha1"))
    _seed("sha1", A=3.0, B=4.5, L=2.0)
    monkeypatch.setattr(
        repositories, "list_label_verdict_priorities",
        lambda db_path: {"L": "must_read"},
    )
    res = reading_queue.build_reading_queue()
    keys = [i["item_key"] for i in res["items"]]
    assert keys[0] == "L"                       # labelled paper pinned to the top
    assert set(keys) == {"A", "B", "L"}         # nothing hidden
    assert res["read_hidden"] == 0
    pinned = next(i for i in res["items"] if i["item_key"] == "L")
    assert pinned["user_priority"] == "must_read"  # surfaced for the UI badge


def test_positive_verdicts_pin_in_priority_order(monkeypatch):
    """Multiple labelled papers pin to the top in priority order (must > should >
    could), preserving the relevance order WITHIN each tier; unlabelled papers
    follow. So the user's explicit ranking wins over the model's."""
    from zotero_summarizer.storage import repositories

    _patch_state(
        monkeypatch,
        _FakeReader([_item("could"), _item("plain"), _item("must"), _item("should")]),
        _FakeGate("sha1"),
    )
    _seed("sha1", could=4.9, plain=4.8, must=1.0, should=1.0)
    monkeypatch.setattr(
        repositories, "list_label_verdict_priorities",
        lambda db_path: {"must": "must_read", "should": "should_read", "could": "could_read"},
    )
    res = reading_queue.build_reading_queue()
    keys = [i["item_key"] for i in res["items"]]
    assert keys == ["must", "should", "could", "plain"]


def test_read_items_hidden_live_and_shown_with_toggle(monkeypatch):
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("R", tags=["🧠"])]), _FakeGate("sha1"))
    _seed("sha1", A=3.0)
    hidden = reading_queue.build_reading_queue(include_read=False)
    assert [i["item_key"] for i in hidden["items"]] == ["A"]
    assert hidden["read_hidden"] == 1
    assert hidden["total_unread"] == 1  # whole-library count = the one unread paper
    shown = reading_queue.build_reading_queue(include_read=True)
    assert "R" in [i["item_key"] for i in shown["items"]]
    assert shown["total_unread"] == 1  # include_read must NOT inflate total_unread


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
        aux={"citation_percentile": 0.75, "max_author_h_index": 20},
    )
    sc = reading_queue.scoring_from_prediction(pred)
    assert sc["composite_score"] == 3.7
    assert {"feature": "semantic_match_specter2", "value": 0.6} in sc["shap_top"]
    # Prestige now derives from the field-normalized percentile (0.75 → 4.0),
    # not the h-index; h-index is kept only as a context input.
    assert sc["prestige_score"] == 4.0


def test_dedup_by_content_keeps_first_and_preserves_order():
    # Same paper, two Zotero copies (distinct keys, identical title, authors in
    # DIFFERENT order/format) → one survives, the FIRST (best-ranked after sort).
    # Title-only key, so author ordering can't defeat it.
    recs = [
        {"item_key": "A", "title": "GlobalDentBench: a benchmark", "authors": "Smith, J; Lee, K"},
        {"item_key": "B", "title": "A totally different paper", "authors": "Doe, J"},
        {"item_key": "C", "title": "globaldentbench: A Benchmark", "authors": "Lee K., Smith J."},  # dup of A (reordered authors)
        {"item_key": "D", "title": "AutoScientists: agent teams", "authors": "Zitnik; Gao; Fang"},
        {"item_key": "E", "title": "AutoScientists: agent teams", "authors": "Gao; Fang; Zitnik"},  # dup of D (reordered)
    ]
    out = reading_queue._dedup_by_content(recs)
    keys = [r["item_key"] for r in out]
    assert keys == ["A", "B", "D"]  # C dup of A, E dup of D; order preserved


def test_dedup_by_content_never_merges_untitled():
    recs = [{"item_key": "A", "title": "", "authors": "X"}, {"item_key": "B", "title": "", "authors": "X"}]
    assert [r["item_key"] for r in reading_queue._dedup_by_content(recs)] == ["A", "B"]


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

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False, prestige_network=True):
        scorable = [it for it in items if it["item_key"] not in self._skip]
        return super().predict(
            scorable, corpus_db_path=corpus_db_path, goals_config=goals_config,
            return_shap=return_shap, prestige_network=prestige_network,
        )


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


def test_rescore_stores_goal_sim_in_cache(monkeypatch):
    """D: goal_sim is computed at rescore time and persisted per entry (float or
    None, key always present), so opening the queue reads it from the cache
    instead of re-running the corpus matmul on every load."""
    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("B")]), _FakeGate("sha1", {"A": 3.0, "B": 4.0}))
    monkeypatch.setattr(reading_queue, "_goal_affinity", lambda keys: {"A": 0.7})
    reading_queue._compute_scores_into_cache("sha1", full=True)
    cached = reading_queue._read_cache("sha1")
    assert cached["A"]["goal_sim"] == 0.7
    assert cached["B"]["goal_sim"] is None  # no goal embedding → None, but key present


def test_full_library_scoring_includes_read_items(monkeypatch):
    """The whole-library scan scores read AND unread items (read item carries a
    🧠 emoji). Needed so every paper has a cached score for the global Zotero rank."""
    reader = _FakeReader([_item("A"), _item("R", tags=["🧠"])])
    _patch_state(monkeypatch, reader, _FakeGate("sha1", {"A": 3.0, "R": 4.0}))
    reading_queue._compute_scores_into_cache("sha1", full=True)
    cached = reading_queue._read_cache("sha1")
    assert cached["A"]["relevance_score"] == 3.0
    assert cached["R"]["relevance_score"] == 4.0  # read item scored too (not skipped)


def test_no_abstract_item_never_enters_cache(monkeypatch):
    """A no-abstract item is unscorable (gate needs an abstract) and must NOT enter
    the cache — it's handled at rank time by sinking to the bottom of the order."""
    noabs = {**_item("N"), "abstract": ""}
    reader = _FakeReader([_item("A"), noabs])
    _patch_state(monkeypatch, reader, _FakeGate("sha1", {"A": 3.0}))
    reading_queue._compute_scores_into_cache("sha1", full=True)
    cached = reading_queue._read_cache("sha1")
    assert "A" in cached
    assert "N" not in cached  # no abstract → skipped entirely


def test_full_rescore_preserves_old_scores_on_crash(monkeypatch):
    """A full Rescore must START FROM the existing cache and overwrite in place
    — never wipe up front. If the gate dies mid-run, the old scores survive on
    disk (the old semantics left a truncated, near-empty cache) and the error
    is surfaced via last_error."""

    class _ExplodingGate(_FakeGate):
        def predict(self, items, **kwargs):
            raise RuntimeError("OpenAlex melted")

    _patch_state(monkeypatch, _FakeReader([_item("A"), _item("B")]), _ExplodingGate("sha1"))
    _seed("sha1", A=2.5, B=4.5)
    reading_queue.try_start()
    with pytest.raises(RuntimeError, match="OpenAlex melted"):
        reading_queue._compute_scores_into_cache("sha1", full=True)
    cached = reading_queue._read_cache("sha1")
    assert cached["A"]["relevance_score"] == 2.5  # old scores intact
    assert cached["B"]["relevance_score"] == 4.5
    assert "OpenAlex melted" in (reading_queue.last_error() or "")


def test_full_rescore_reattempts_everything_and_purges_departed(monkeypatch):
    """full=True re-attempts EVERY item (stale scores get replaced, prior
    sentinels retried) and — only after the pass completes — purges cache
    entries for items that left the library, so deletions don't linger."""
    reader = _FakeReader([_item("A"), _item("B")])
    _patch_state(monkeypatch, reader, _FakeGate("sha1", {"A": 3.7, "B": 4.1}))
    _seed("sha1", A=1.0, GONE=2.0)  # stale score for A; GONE left the library
    reading_queue._compute_scores_into_cache("sha1", full=True)
    cached = reading_queue._read_cache("sha1")
    assert cached["A"]["relevance_score"] == 3.7  # re-attempted, not kept stale
    assert cached["B"]["relevance_score"] == 4.1
    assert "GONE" not in cached  # purged after the successful pass


def test_blended_sort_sinks_unscored_to_bottom():
    """_blended_sort handles a mixed list (scored + None-relevance) in one pass:
    scored papers rank on top, unscored sink to the bottom ordered by date."""
    recs = [
        {"item_key": "lo", "relevance_score": 2.0, "goal_sim": None, "date_added": "2026-01-01"},
        {"item_key": "none_old", "relevance_score": None, "goal_sim": None, "date_added": "2026-01-01"},
        {"item_key": "hi", "relevance_score": 4.5, "goal_sim": None, "date_added": "2026-01-01"},
        {"item_key": "none_new", "relevance_score": None, "goal_sim": None, "date_added": "2026-02-01"},
    ]
    reading_queue._blended_sort(recs)
    keys = [r["item_key"] for r in recs]
    assert keys[:2] == ["hi", "lo"]              # scored on top, best first
    assert set(keys[2:]) == {"none_new", "none_old"}  # unscored at the bottom
    assert keys[2] == "none_new"                 # newer unscored first (date desc)


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
