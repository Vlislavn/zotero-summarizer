"""Acquire-before-score rescue for L1-hide candidates that lack an abstract (G10).

Regression for the measured finding: of 5 L1-only auto-quality hides, ALL were abstract-less
(scored on title alone); 1 was a clear false-positive (L8UUKELJ, both judges=3) and 4 were
borderline-unstable (judges split 2-vs-3). The scale's 2 = "limited rigor" is a *quality*
judgment, not relevance — on a title-only score it catches on-topic-but-thin papers, not "trash".
So before the L1 floor hides such a row, re-score it on the full text (grounded); the hide then
stands only if the re-score is still <= floor.
"""
from __future__ import annotations

from types import SimpleNamespace

from zotero_summarizer.services.triage.feeds import _rescue_l1

_TITLE = "Multi-agent AI systems outperform human teams in creativity"  # a real G10 hide


def _cfg(**over):
    base = dict(enabled=True, max_per_tick=3, min_abstract_chars=120)
    base.update(over)
    return SimpleNamespace(**base)


def _patch_state(monkeypatch, cfg):
    state = SimpleNamespace(app_state=SimpleNamespace(config=SimpleNamespace(recover_abstract=cfg)))
    monkeypatch.setattr(_rescue_l1, "get_state", lambda: state)


def _cand(score, abstract):
    """A triaged candidate: (item, cand) pair shape, cand.summary.relevance_score = score."""
    item = {"item_key": "K", "title": _TITLE, "abstract": abstract}
    cand = SimpleNamespace(summary=SimpleNamespace(relevance_score=score), feed_item=item)
    return item, cand


def _rescued_cand(score):
    """A re-scored candidate (what _rescue_one returns): grounded score on full text."""
    return SimpleNamespace(summary=SimpleNamespace(relevance_score=score), feed_item={"item_key": "K"}, composite_score=3.8)


def test_rescues_abstractless_l1_candidate(monkeypatch):
    """score<=floor + no abstract → re-scored (grounded). The re-score clears the floor → survives."""
    _patch_state(monkeypatch, _cfg())
    monkeypatch.setattr(_rescue_l1, "_rescue_one", lambda item, *, tick_id: _rescued_cand(4))

    pair = _cand(score=2, abstract="")  # title-only score 2, no abstract → L1 hide candidate
    out, n = _rescue_l1.recover_abstractless_l1_candidates([pair], tick_id="t", llm_floor=2)

    assert n == 1
    assert out[0][1].summary.relevance_score == 4  # replaced with the grounded re-score


def test_skips_when_abstract_present(monkeypatch):
    """A row WITH a usable abstract was scored on content — no rescue (the score is grounded)."""
    _patch_state(monkeypatch, _cfg())
    called = []
    monkeypatch.setattr(_rescue_l1, "_rescue_one", lambda item, *, tick_id: called.append(item) or _rescued_cand(4))

    real_abstract = "We present a multi-agent LLM system for clinical hypothesis generation. " * 4
    pair = _cand(score=2, abstract=real_abstract)
    out, n = _rescue_l1.recover_abstractless_l1_candidates([pair], tick_id="t", llm_floor=2)

    assert n == 0
    assert called == []  # never fetched
    assert out[0][1].summary.relevance_score == 2  # unchanged


def test_skips_when_score_above_floor(monkeypatch):
    """score > floor isn't a hide candidate — left alone (the gate wouldn't hide it anyway)."""
    _patch_state(monkeypatch, _cfg())
    monkeypatch.setattr(_rescue_l1, "_rescue_one", lambda item, *, tick_id: _rescued_cand(4))

    pair = _cand(score=3, abstract="")  # above floor, no rescue needed
    out, n = _rescue_l1.recover_abstractless_l1_candidates([pair], tick_id="t", llm_floor=2)

    assert n == 0
    assert out[0][1].summary.relevance_score == 3  # unchanged


def test_no_full_text_keeps_verdict(monkeypatch):
    """_rescue_one returns None (no fetchable full text) → title-grounded verdict stands."""
    _patch_state(monkeypatch, _cfg())
    monkeypatch.setattr(_rescue_l1, "_rescue_one", lambda item, *, tick_id: None)

    pair = _cand(score=2, abstract="")
    out, n = _rescue_l1.recover_abstractless_l1_candidates([pair], tick_id="t", llm_floor=2)

    assert n == 0
    assert out[0][1].summary.relevance_score == 2  # original verdict stands (grounded negative)


def test_max_per_tick_caps_rescues(monkeypatch):
    """Two eligible candidates, cap=1 → one rescued, one deferred (verdict stands)."""
    _patch_state(monkeypatch, _cfg(max_per_tick=1))
    monkeypatch.setattr(_rescue_l1, "_rescue_one", lambda item, *, tick_id: _rescued_cand(4))

    a = _cand(score=2, abstract="")
    b = _cand(score=1, abstract="")
    out, n = _rescue_l1.recover_abstractless_l1_candidates([a, b], tick_id="t", llm_floor=2)

    assert n == 1
    scores = [c.summary.relevance_score for _, c in out]
    assert 4 in scores and (2 in scores or 1 in scores)  # one re-scored, one left


def test_disabled_is_noop(monkeypatch):
    """recover_abstract off → no behavior change (the L1 floor acts as before)."""
    _patch_state(monkeypatch, _cfg(enabled=False))
    monkeypatch.setattr(_rescue_l1, "_rescue_one", lambda item, *, tick_id: _rescued_cand(4))

    pair = _cand(score=2, abstract="")
    out, n = _rescue_l1.recover_abstractless_l1_candidates([pair], tick_id="t", llm_floor=2)

    assert n == 0
    assert out == [pair]  # untouched
