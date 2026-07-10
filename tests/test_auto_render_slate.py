"""`_auto_render_slate` — the daemon step that builds the heavy brief for the TOP feed
papers. Pins the selection contract: only ALREADY-reviewed feed keys (so the brief folds
the review in, never racing it), top-K by composite rank, skipping already-rendered ones.
"""
from __future__ import annotations

import types

import pytest

from zotero_summarizer.services import _common
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.library import deep_review, paper_render
from zotero_summarizer.services.triage import daily_select
from zotero_summarizer.services.triage.feeds import _tick

_K = lambda n: f"feed:d:{str(n) * 64}"  # noqa: E731 — a valid-shaped stable_feed_key per test


def _paper(sfk, score):
    return types.SimpleNamespace(stable_feed_key=sfk, composite_score=score)


@pytest.fixture
def _wire(monkeypatch):
    monkeypatch.setattr(_tick, "_load_config", lambda: {"quality_review": {"render_on_tick_k": 2}})
    monkeypatch.setattr(_common, "settings", lambda: types.SimpleNamespace(triage_db_path="x"))
    built: list[str] = []
    monkeypatch.setattr(paper_render, "start_build", lambda key, **kw: built.append(key))
    return built


def _set_slate(monkeypatch, papers):
    monkeypatch.setattr(daily_select, "assemble_daily_slate", lambda **kw: types.SimpleNamespace(papers=papers))


def test_renders_top_reviewed_unrendered_in_rank_order(_wire, monkeypatch):
    _set_slate(monkeypatch, [_paper(_K(1), 3.0), _paper(_K(2), 5.0), _paper(_K(3), 1.0)])
    monkeypatch.setattr(deep_review, "cached_review_keys", lambda: {_K(1), _K(2)})  # K(3) NOT reviewed
    monkeypatch.setattr(paper_render, "_read_state", lambda key: None)             # none rendered yet

    _tick._auto_render_slate("t1")

    # top-2 of the reviewed set, highest composite first; the un-reviewed K(3) is excluded.
    assert _wire == [_K(2), _K(1)]


def test_skips_already_rendered(_wire, monkeypatch):
    _set_slate(monkeypatch, [_paper(_K(2), 5.0), _paper(_K(1), 3.0)])
    monkeypatch.setattr(deep_review, "cached_review_keys", lambda: {_K(1), _K(2)})
    # K(2) already has a completed render → skip it; K(1) still builds.
    monkeypatch.setattr(paper_render, "_read_state",
                        lambda key: {"status": "completed"} if key == _K(2) else None)

    _tick._auto_render_slate("t1")

    assert _wire == [_K(1)]


def test_disabled_when_k_zero(monkeypatch):
    monkeypatch.setattr(_tick, "_load_config", lambda: {"quality_review": {"render_on_tick_k": 0}})
    built: list[str] = []
    monkeypatch.setattr(paper_render, "start_build", lambda key, **kw: built.append(key))
    _tick._auto_render_slate("t1")
    assert built == []


def test_render_on_tick_k_default_is_three():
    assert _default_goals_config().quality_review.render_on_tick_k == 3
