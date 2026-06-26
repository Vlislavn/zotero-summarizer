"""P3 — the GUID↔item_key quality bridge + the allocator's model-role confinement.

Pins the two safety properties a 10-expert review demanded before quality counts
on the Today slate:
  * the bridge joins ONLY via ``materialized_zotero_key`` (the v1 trap was a
    silently always-0 join through the feed GUID),
  * the capped quality lift is applied ONLY in the FLOORED model role — it cannot
    lift a below-bar paper into the slate, and the un-floored discovery roles
    (surprise/diversity) stay quality-free.
"""
from __future__ import annotations

from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.triage.daily_select import _allocation, _candidate


def _model_cand(id_: int, composite: float, rank: float, band: str | None = None,
                grade: str | None = None, affinity: float = 1.0) -> dict:
    return {
        "id": id_, "composite_score": composite, "rank_score": rank,
        "corpus_affinity": affinity,
        "quality": {"quality_band": band, "grade": grade} if (band or grade) else {},
    }


def test_attach_quality_bridges_only_via_materialized_key(monkeypatch) -> None:
    monkeypatch.setattr(deep_review, "_read_all", lambda: {
        "ZK1": {"quality": {"quality_band": "highlight", "grade": "A"}},
    })
    cands = [
        {"materialized_zotero_key": "ZK1", "quality": {}},
        {"materialized_zotero_key": None, "quality": {}},   # GUID-only → never a false join
        {"materialized_zotero_key": "MISSING", "quality": {}},
    ]
    matched = _candidate.attach_quality_from_reviews(cands)
    assert matched == 1
    assert cands[0]["quality"] == {"quality_band": "highlight", "grade": "A"}
    assert cands[1]["quality"] == {}
    assert cands[2]["quality"] == {}


def test_pick_model_floats_highlight_within_floored_role(monkeypatch) -> None:
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "1")
    # Equal base rank_score, both above the could_read floor → the highlight floats.
    neutral = _model_cand(2, 4.0, 0.50, band="neutral")
    highlight = _model_cand(1, 4.0, 0.50, band="highlight")
    picks = _allocation._pick_model([neutral, highlight], 2, set())
    assert [c["id"] for c in picks] == [1, 2]


def test_pick_model_grade_only_lift_by_default(monkeypatch) -> None:
    # Band-primary OFF (shipped default) → grade-only lift: an A floats above an
    # ungraded peer at equal base rank_score, purely on the grade.
    monkeypatch.delenv("ZS_QUALITY_BAND_PRIMARY", raising=False)
    a = _model_cand(1, 4.0, 0.50, grade="A")
    ungraded = _model_cand(2, 4.0, 0.50)
    picks = _allocation._pick_model([ungraded, a], 2, set())
    assert [c["id"] for c in picks] == [1, 2]


def test_pick_model_floor_excludes_below_bar_even_if_highlight(monkeypatch) -> None:
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "1")
    # A below-floor paper (composite < could_read) is excluded BEFORE quality —
    # the lift can never surface a below-the-bar paper (floor is on raw composite).
    below = _model_cand(1, 1.0, 0.9, band="highlight")
    above = _model_cand(2, 4.0, 0.1, band="neutral")
    picks = _allocation._pick_model([below, above], 5, set())
    assert [c["id"] for c in picks] == [2]


def test_pick_diversity_ignores_quality(monkeypatch) -> None:
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "1")
    # Both off-library (affinity < 0). b has the higher BASE rank_score but is
    # flagged; if quality leaked into diversity the flag (-) would sink it below
    # a's highlight (+). It must NOT — diversity is quality-free, so b still wins.
    a = _model_cand(1, 3.0, 0.50, band="highlight", affinity=-0.2)
    b = _model_cand(2, 3.0, 0.60, band="flag", affinity=-0.1)
    picks = _allocation._pick_diversity([a, b], 1, set())
    assert picks[0]["id"] == 2
