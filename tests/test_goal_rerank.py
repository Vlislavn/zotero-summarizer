"""Goal-aware re-rank: blend goal-text similarity into the queue order so
on-goal papers the gate under-ranks float up (banding stays from the gate)."""
from __future__ import annotations

import json

from zotero_summarizer.services.library import _ranking
from zotero_summarizer.storage.corpus import EmbeddingCache


def _rec(key, rel, goal, *, prestige=None, known=False):
    # prestige_score (1–5) + prestige_known mirror the reading-queue rec shape; an
    # unset prestige (known=False) carries no quality evidence, so the prestige
    # term in the blend stays inert for that row.
    return {
        "item_key": key, "relevance_score": rel, "goal_sim": goal,
        "date_added": "2026-05-01", "prestige_score": prestige, "prestige_known": known,
    }


def test_blended_sort_floats_on_goal_item_above_higher_relevance():
    # item B has mid relevance but the strongest goal match; with the 0.4 goal
    # weight it should outrank A (highest relevance, off-goal). C (low both) last.
    recs = [_rec("A", 4.0, 0.0), _rec("B", 3.0, 0.5), _rec("C", 2.0, 0.1)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A", "C"]


def test_quality_grade_gives_a_bounded_lift_within_neighbourhood():
    # Equal relevance + no goal/prestige signal → the deep-review grade is the only
    # differentiator: an A-graded paper floats above an ungraded one (user request).
    a = {**_rec("A_GRADE", 3.0, None), "quality_grade": "A"}
    none = {**_rec("UNGRADED", 3.0, None)}
    recs = [none, a]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["A_GRADE", "UNGRADED"]


def test_quality_lift_cannot_override_a_full_relevance_band():
    # The lift is BOUNDED: a D-graded high-relevance paper still outranks an
    # A-graded low-relevance one — quality nudges within a band, never across.
    hi = {**_rec("HI_REL_D", 4.5, None), "quality_grade": "D"}
    lo = {**_rec("LO_REL_A", 2.5, None), "quality_grade": "A"}
    recs = [lo, hi]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["HI_REL_D", "LO_REL_A"]


def test_blended_sort_prestige_lifts_equal_relevance_paper():
    # Equal relevance, no goal signal → prestige is the only differentiator: the
    # high-prestige paper (strong author/venue) floats above the low-prestige one.
    recs = [_rec("LOW", 3.0, None, prestige=1.0, known=True),
            _rec("HIGH", 3.0, None, prestige=5.0, known=True)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["HIGH", "LOW"]


def test_blended_sort_unknown_prestige_treated_as_typical_not_penalised():
    # Cold-start / uncited work (no KNOWN prestige) must rank as TYPICAL — above a
    # genuinely low-prestige known paper, never penalised for missing evidence.
    recs = [
        _rec("HIGH", 3.0, None, prestige=5.0, known=True),
        _rec("UNKNOWN", 3.0, None, prestige=None, known=False),
        _rec("LOW", 3.0, None, prestige=1.0, known=True),
        _rec("MID", 3.0, None, prestige=3.0, known=True),
    ]
    _ranking._blended_sort(recs)
    order = [r["item_key"] for r in recs]
    assert order[0] == "HIGH"                            # best quality on top
    assert order[-1] == "LOW"                            # known-low sinks
    assert order.index("UNKNOWN") < order.index("LOW")   # cold-start NOT penalised


def test_blended_sort_prestige_stays_secondary_to_relevance():
    # A much higher-relevance low-prestige paper still beats a low-relevance
    # high-prestige one — prestige is a lift, not a takeover (relevance primary).
    recs = [_rec("HIREL", 5.0, None, prestige=1.0, known=True),
            _rec("HIPRES", 1.0, None, prestige=5.0, known=True)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["HIREL", "HIPRES"]


def test_blended_sort_all_unknown_prestige_equals_goal_blend():
    # Prestige fields present but all unknown → term inert; identical to the pure
    # goal-blend order (the measured baseline), so prestige never changes a library
    # with no OpenAlex coverage.
    recs = [_rec("A", 4.0, 0.0, known=False), _rec("B", 3.0, 0.5, known=False),
            _rec("C", 2.0, 0.1, known=False)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A", "C"]


def test_blended_sort_unscored_sink_to_bottom():
    recs = [_rec("A", None, 0.9), _rec("B", 3.0, 0.0)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A"]


def test_blended_sort_no_goal_signal_falls_back_to_relevance():
    recs = [_rec("A", 2.0, None), _rec("B", 4.0, None)]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["B", "A"]  # pure relevance order


def test_band_primary_mode_floats_highlight_and_pins_uncertain(monkeypatch):
    # With the Phase-2 band-primary arm enabled (env override, consumer-side), a
    # highlight floats above an equal-relevance neutral, a flag sinks, and an
    # UNCERTAIN paper stays exactly where neutral would (never buried).
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "1")
    hi = {**_rec("HI", 3.0, None), "quality_band": "highlight"}
    neutral = {**_rec("NEUTRAL", 3.0, None), "quality_band": "neutral"}
    flag = {**_rec("FLAG", 3.0, None), "quality_band": "flag"}
    uncertain = {**_rec("UNCERTAIN", 3.0, None), "quality_band": "uncertain"}
    recs = [flag, neutral, uncertain, hi]
    _ranking._blended_sort(recs)
    order = [r["item_key"] for r in recs]
    assert order[0] == "HI"                                  # highlight floats
    assert order[-1] == "FLAG"                               # flag sinks
    # uncertain and neutral both got exactly 0.0 → tie, original order preserved.
    assert order.index("UNCERTAIN") < order.index("FLAG")    # uncertain not demoted


def test_band_primary_off_by_default_ignores_band(monkeypatch):
    # No env, no config → the shipped grade-only default: the band is inert, so a
    # bare highlight does NOT outrank an equal-relevance neutral on band alone.
    monkeypatch.delenv("ZS_QUALITY_BAND_PRIMARY", raising=False)
    hi = {**_rec("HI", 3.0, None), "quality_band": "highlight"}
    neutral = {**_rec("NEUTRAL", 3.0, None), "quality_band": "neutral"}
    recs = [neutral, hi]
    _ranking._blended_sort(recs)
    assert [r["item_key"] for r in recs] == ["NEUTRAL", "HI"]  # stable; band ignored


def test_blended_sort_is_order_only_never_mutates_relevance(monkeypatch):
    # The derivation==prediction invariant at the sort seam: the quality lift only
    # REORDERS; it never touches relevance_score (banding is computed from that raw
    # score elsewhere, so it stays byte-identical with or without the bonus).
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "1")
    recs = [
        {**_rec("A", 4.0, None), "quality_band": "flag"},
        {**_rec("B", 3.0, None), "quality_band": "highlight"},
    ]
    before = {r["item_key"]: r["relevance_score"] for r in recs}
    _ranking._blended_sort(recs)
    after = {r["item_key"]: r["relevance_score"] for r in recs}
    assert before == after


def test_goal_affinity_for_items_returns_max_cosine(tmp_path):
    cache = EmbeddingCache(tmp_path / "corpus.db", "stub-model")
    conn = cache._conn()
    try:
        # Two goals (unit axes) + items aligned to each.
        conn.execute("INSERT INTO goal_embeddings (goal, embedding_json) VALUES (?, ?)",
                     ("agents", json.dumps([1.0, 0.0, 0.0])))
        conn.execute("INSERT INTO goal_embeddings (goal, embedding_json) VALUES (?, ?)",
                     ("clinical", json.dumps([0.0, 1.0, 0.0])))
        for iid, vec in [("on_goal", [0.0, 1.0, 0.0]), ("off_goal", [0.0, 0.0, 1.0])]:
            conn.execute(
                "INSERT INTO corpus_embeddings (item_id, title, content_hash, embedding_json) VALUES (?,?,?,?)",
                (iid, iid, "h", json.dumps(vec)),
            )
        conn.commit()
    finally:
        conn.close()
    out = cache.goal_affinity_for_items(["on_goal", "off_goal", "missing"])
    assert out["on_goal"] == 1.0          # exactly matches the "clinical" goal axis
    assert abs(out["off_goal"]) < 1e-6     # orthogonal to both goals
    assert "missing" not in out            # no cached embedding → omitted
