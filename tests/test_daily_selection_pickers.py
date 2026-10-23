"""Synthetic picker observations and exact interleave redaction; no live models."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest

from tests._daily_select_helpers import _DEFAULT_NOW, _create_db, _insert, seed_reviews
from zotero_summarizer.api.routes.daily import _slate_to_payload
from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.triage import daily_select as daily
from zotero_summarizer.services.triage.daily_select import _allocation as allocator
from zotero_summarizer.services.triage.daily_select import _candidate as candidate
from zotero_summarizer.services.triage.daily_select._selection_evidence import picker_observations
from zotero_summarizer.storage.interleave import fetch_interleave_log


def _cand(number, *, score=4.0, rank=0.5, quality=None, affinity=0.2,
          surprise=0.0, relevance=None):
    """Handcrafted operational keys do not assert cohort/source provenance."""
    cand = candidate.make_candidate({
        "id": number, "guid": f"P{number}", "title": f"Paper {number}",
        "decision": "awaiting_review", "composite_score": score,
        "surprise_score": surprise, "corpus_affinity": affinity,
        "shap_contribs_json": "",
    })
    cand["rank_score"] = rank
    cand["quality"] = {} if quality is None else quality
    cand["relevance_score"] = relevance
    return cand


def _watch_policy(monkeypatch, policies):
    values = iter(policies)
    reads, bonuses = [], []
    original = allocator.quality_bonus

    def policy():
        value = next(values)
        reads.append(value)
        return value

    def bonus(band, grade, *, use_band):
        value = original(band, grade, use_band=use_band)
        bonuses.append((band, grade, use_band, value))
        return value

    monkeypatch.setattr(allocator, "band_primary_enabled", policy)
    monkeypatch.setattr(allocator, "quality_bonus", bonus)
    return reads, bonuses


@pytest.mark.parametrize("use_band,expected_ids,expected_bonuses", [
    (False, [2, 1], [-0.02, 0.06]),
    (True, [1, 2], [0.05, -0.03]),
])
def test_actual_grade_or_band_bonus_reverses_same_band_order(
    monkeypatch, use_band, expected_ids, expected_bonuses,
):
    pool = [
        _cand(1, rank=0.52, quality={"grade": "D", "quality_band": "highlight"}),
        _cand(2, rank=0.50, quality={"grade": "A", "quality_band": "flag"}),
        _cand(3, score=1.9, rank=99, quality={"grade": "A"}),
    ]
    reads, bonuses = _watch_policy(monkeypatch, [use_band])
    papers, empty = allocator.allocate(candidate_pool=pool, roles={"model": 2}, K=2)
    assert [p.item_id for p in papers] == expected_ids and empty == []
    assert reads == [use_band]
    assert [b[2] for b in bonuses] == [use_band, use_band]
    assert [b[3] for b in bonuses] == pytest.approx(expected_bonuses)
    for paper in papers:
        index = paper.item_id - 1
        assert paper.selection_evidence["selection"] == {
            "requested_role": "model", "basis": "model_adjusted",
            "key": pytest.approx(pool[index]["rank_score"] + expected_bonuses[index]),
            "quality_bonus": pytest.approx(expected_bonuses[index]),
            "bonus_policy": "band" if use_band else "grade", "band_restricted": True,
        }
        assert paper.composite_score == 4.0


def test_model_preserves_interleaved_raw_band_slots_not_global_key_order(monkeypatch):
    pool = [
        _cand(1, score=3.6, rank=0.90, quality={"grade": "D"}),
        _cand(2, score=4.6, rank=0.89, quality={"grade": "A"}),
        _cand(3, score=3.6, rank=0.88, quality={"grade": "A"}),
        _cand(4, score=4.6, rank=0.87, quality={"grade": "D"}),
    ]
    reads, bonuses = _watch_policy(monkeypatch, [False])
    papers, empty = allocator.allocate(candidate_pool=pool, roles={"model": 4}, K=4)
    assert [p.item_id for p in papers] == [3, 2, 1, 4] and empty == []
    assert [p.composite_score for p in papers] == [3.6, 4.6, 3.6, 4.6]
    selections = [p.selection_evidence["selection"] for p in papers]
    assert [s["key"] for s in selections] == pytest.approx([0.94, 0.95, 0.88, 0.85])
    assert selections[0]["key"] < selections[1]["key"]
    assert all(s["band_restricted"] and s["bonus_policy"] == "grade" for s in selections)
    assert reads == [False] and len(bonuses) == 4


@pytest.mark.parametrize("requested", ["surprise", "diversity"])
def test_each_fallback_attempt_captures_its_actual_policy_and_requested_role(
    monkeypatch, requested,
):
    pool = [
        _cand(1, score=4.8, rank=0.60, quality={"grade": "A", "quality_band": "neutral"}),
        _cand(2, score=4.0, rank=0.50, quality={"grade": "A", "quality_band": "neutral"}),
    ]
    reads, bonuses = _watch_policy(monkeypatch, [False, True])
    papers, empty = allocator.allocate(
        candidate_pool=pool, roles={"model": 1, requested: 1}, K=2,
    )
    assert reads == [False, True]
    assert [b[2] for b in bonuses] == [False, False, True]
    assert [p.item_id for p in papers] == [1, 2]
    assert [p.role for p in papers] == ["model", "model_fallback"]
    assert empty == [requested]
    for paper, role, policy, key, bonus in zip(
        papers, ["model", requested], ["grade", "band"], [0.66, 0.50], [0.06, 0.0],
    ):
        assert paper.selection_evidence["selection"] == {
            "requested_role": role, "basis": "model_adjusted", "key": pytest.approx(key),
            "quality_bonus": bonus, "bonus_policy": policy, "band_restricted": True,
        }


def test_quality_first_uses_actual_cohort_key_floor_and_no_additional_bonus(monkeypatch):
    pool = [
        _cand(1, score=1.0, quality={"grade": "A"}),
        _cand(2, score=4.8, quality={"grade": "D"}, relevance=4.0),
        _cand(3, score=4.9, quality={"grade": "B"}, relevance=2.0),
        _cand(4, score=4.7, quality={"grade": "B"}, relevance=2.01),
    ]
    candidate.attach_rank_scores(pool, quality_first=True)
    reads, bonuses = _watch_policy(monkeypatch, [])
    papers, empty = allocator.allocate(
        candidate_pool=pool, roles={"model": 3}, K=3, quality_first=True,
    )
    assert [p.item_id for p in papers] == [1, 4, 2] and empty == []
    assert reads == [] and bonuses == []
    for paper, key in zip(papers, [0.75, 0.5625, 0.0]):
        observed = paper.selection_evidence
        assert observed["mode"] == "quality_first"
        assert observed["rank"] == {"basis": "quality_first", "key": key}
        assert observed["selection"] == {
            "requested_role": "model", "basis": "cohort", "key": key,
            "quality_bonus": None, "bonus_policy": "not_used", "band_restricted": False,
        }


@pytest.mark.parametrize("quality_first", [False, True])
def test_discovery_replaces_model_trace_with_actual_scalar_or_cohort_key(
    monkeypatch, quality_first,
):
    pool = [
        _cand(4, score=4.5, quality={"grade": "A"}),
        _cand(1, score=4.0, quality={"grade": "A"}),
        _cand(2, score=3.0, quality={"grade": "D"}, affinity=-0.2, surprise=0.9),
        _cand(3, score=2.5, quality={"grade": "B"}, affinity=-0.3, surprise=0.8),
    ]
    candidate.attach_rank_scores(pool, quality_first=quality_first)
    for cand in pool[2:]:
        cand["selection_evidence"] = picker_observations(
            cand["selection_evidence"], requested_role="model", basis="model_adjusted",
            key=99.0, bonus=0.9, bonus_policy="band", band_restricted=True,
        )
    before = deepcopy([{k: v for k, v in c.items() if k != "selection_evidence"} for c in pool])
    reads, bonuses = _watch_policy(monkeypatch, [False])
    papers, empty = allocator.allocate(
        candidate_pool=pool, roles={"model": 1, "surprise": 1, "diversity": 1},
        K=3, quality_first=quality_first,
    )
    assert [p.item_id for p in papers] == [4, 2, 3] and empty == []
    assert [p.role for p in papers] == ["model", "surprise", "diversity"]
    assert reads == ([] if quality_first else [False])
    assert len(bonuses) == (0 if quality_first else 4)
    for paper, cand, basis, key in [
        (papers[1], pool[2], "surprise", 0.9),
        (papers[2], pool[3], "cohort", pool[3]["rank_score"]),
    ]:
        assert paper.selection_evidence["selection"] == {
            "requested_role": paper.role, "basis": basis, "key": key,
            "quality_bonus": None, "bonus_policy": "not_used", "band_restricted": False,
        }
        assert cand["selection_evidence"]["selection"] == paper.selection_evidence["selection"]
    assert papers[1].selection_evidence["selection"]["key"] != pool[2]["rank_score"]
    assert [{k: v for k, v in c.items() if k != "selection_evidence"} for c in pool] == before


@pytest.mark.parametrize("K", [1, 2, 5])
def test_exact_ties_role_quotas_and_cap_keep_legacy_order_without_quality(monkeypatch, K):
    pool = [_cand(i, affinity=-0.1, surprise=0.9) for i in (1, 2, 3)]
    reads, bonuses = _watch_policy(monkeypatch, [False])
    papers, empty = allocator.allocate(
        candidate_pool=pool, roles={"model": 1, "surprise": 1, "diversity": 1}, K=K,
    )
    assert [p.item_id for p in papers] == [1, 2, 3][:K] and empty == []
    assert [p.role for p in papers] == ["model", "surprise", "diversity"][:K]
    assert reads == [False] and len(bonuses) == 3
    selection = papers[0].selection_evidence["selection"]
    assert selection["key"] == 0.5 and selection["quality_bonus"] == 0.0
    assert selection["bonus_policy"] == "grade" and selection["band_restricted"] is True


@pytest.mark.parametrize("role", ["model", "surprise", "diversity", "model_fallback"])
def test_direct_conversion_discards_all_prior_context_and_does_not_infer_role(role):
    cand = _cand(1)
    candidate.attach_rank_scores([cand], quality_first=True)
    cand["selection_evidence"] = picker_observations(
        cand["selection_evidence"], requested_role="model", basis="cohort", key=0.375,
    )
    before = deepcopy(cand)
    first = allocator._to_slate_paper(cand, role=role)
    second = allocator._to_slate_paper(cand, role=role)
    assert first.role == role
    assert first.selection_evidence == second.selection_evidence == {"basis": "not_observed"}
    assert first.selection_evidence is not second.selection_evidence
    first.selection_evidence["basis"] = "changed"
    assert cand == before and second.selection_evidence == {"basis": "not_observed"}


def test_picker_projection_is_pure_and_selected_evidence_has_no_nested_aliases(monkeypatch):
    cand = _cand(1)
    candidate.attach_rank_scores([cand])
    initial = cand["selection_evidence"]
    before = deepcopy(initial)
    trace = picker_observations(
        initial, requested_role="model", basis="model_adjusted", key=0.5,
        bonus=0.0, bonus_policy="grade", band_restricted=True,
    )
    assert initial == before and trace is not initial
    for field in ("stored", "review", "rank"):
        assert trace[field] is not initial[field]
    cand["selection_evidence"] = trace
    _watch_policy(monkeypatch, [False, False])
    first, _ = allocator.allocate(candidate_pool=[cand], roles={"model": 1}, K=1)
    second, _ = allocator.allocate(candidate_pool=[cand], roles={"model": 1}, K=1)
    home = cand["selection_evidence"]
    saved = deepcopy(first[0].selection_evidence)
    for field in ("stored", "review", "rank", "selection"):
        assert first[0].selection_evidence[field] is not home[field]
        assert first[0].selection_evidence[field] is not second[0].selection_evidence[field]
    home["stored"]["composite_recorded"] = False
    home["review"]["join"] = "changed"
    home["rank"]["key"] = 88.0
    home["selection"]["key"] = 99.0
    assert first[0].selection_evidence == second[0].selection_evidence == saved
    first[0].selection_evidence["stored"]["reading_priority"] = "changed"
    assert second[0].selection_evidence == saved and initial == before


@pytest.mark.parametrize("key", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_observation_does_not_reject_or_change_operational_key(monkeypatch, key):
    cand = _cand(1, rank=key, quality={"grade": "A"})
    reads, bonuses = _watch_policy(monkeypatch, [False])
    papers, empty = allocator.allocate(candidate_pool=[cand], roles={"model": 1}, K=1)
    assert len(papers) == 1 and empty == []
    assert cand["rank_score"] is key
    assert papers[0].composite_score == 4.0
    assert papers[0].selection_evidence["selection"]["key"] is None
    assert papers[0].selection_evidence["selection"]["quality_bonus"] == 0.06
    assert reads == [False] and len(bonuses) == 1


@pytest.fixture
def interleave_case(tmp_path, monkeypatch):
    db = tmp_path / "triage.db"
    _create_db(db)
    seed_reviews(monkeypatch, {"ZK-A": "A", "ZK-D": "D"})
    for key, review_key, goal in [("A-OFF", "ZK-A", 0.05), ("D-ON", "ZK-D", 0.95)]:
        _insert(db, item_key=key, decision="awaiting_review", composite_score=3.0,
                corpus_affinity=0.2, materialized_zotero_key=review_key, goal_sims={"g": goal})
    monkeypatch.setattr(daily, "rank_interleave_enabled", lambda: True)
    monkeypatch.setattr(allocator, "band_primary_enabled", lambda: False)
    original = daily.assemble_daily_slate
    arms = []

    def capture_arm(**kwargs):
        arm = original(**kwargs)
        arms.append((kwargs["quality_first"], arm, deepcopy(asdict(arm))))
        return arm

    monkeypatch.setattr(daily, "assemble_daily_slate", capture_arm)
    return db, original, arms


def test_interleave_exact_home_preserves_drafting_arm_fields_metadata_and_log(interleave_case):
    db, build, arms = interleave_case
    slate = build(db_path=db, K=5, now=_DEFAULT_NOW)
    assert [mode for mode, _, _ in arms] == [False, True]
    a0, a2 = arms[0][1], arms[1][1]
    assert [p.item_key for p in a0.papers] == ["D-ON", "A-OFF"]
    assert [p.item_key for p in a2.papers] == ["A-OFF", "D-ON"]
    assert [a0.selection_policy, a2.selection_policy] == ["control", "quality_first"]
    day = _DEFAULT_NOW.astimezone().date().isoformat()
    picks = daily.team_draft_merge(
        [p.item_id for p in a0.papers], [p.item_id for p in a2.papers], 5, seed=day,
    )
    arm_papers = {"a": a0.papers, "both": a0.papers, "b": a2.papers}
    expected_log = []
    for position, (paper, pick) in enumerate(zip(slate.papers, picks)):
        source = next(p for p in arm_papers[pick.team] if p.item_id == pick.item)
        actual_old, source_old = asdict(paper), asdict(source)
        actual_old.pop("selection_evidence")
        source_old.pop("selection_evidence")
        assert actual_old == source_old and paper is not source
        assert paper.selection_evidence == {"mode": "interleave", "basis": "unavailable_blinded"}
        assert paper.selection_evidence is not source.selection_evidence
        expected_log.append({
            "item_id": pick.item, "team": {"a": "a0", "b": "a2", "both": "both"}[pick.team],
            "pair_id": pick.pair_id, "position": position,
        })
    assert len(slate.papers) == len(picks) == 2
    log = fetch_interleave_log(db)
    assert [{k: row[k] for k in ("item_id", "team", "pair_id", "position")}
            for row in sorted(log, key=lambda row: row["position"])] == expected_log
    assert {row["team"] for row in log} == {"a0", "a2"}
    assert len({row["pair_id"] for row in log}) == 1 and log[0]["pair_id"] is not None
    actual_root, control_root = asdict(slate), asdict(a0)
    for root in (actual_root, control_root):
        root.pop("papers")
        root.pop("selection_policy")
    control_root["fellback_to_recent"] = a0.fellback_to_recent or a2.fellback_to_recent
    assert actual_root == control_root
    payload = _slate_to_payload(slate)
    assert payload["selection_policy"] == slate.selection_policy == "interleave"
    assert all(p["selection_evidence"] == {"mode": "interleave", "basis": "unavailable_blinded"}
               for p in payload["papers"])
    assert all(asdict(arm) == saved for _, arm, saved in arms)
    slate.papers[0].selection_evidence["mode"] = "changed"
    assert slate.papers[1].selection_evidence == {"mode": "interleave", "basis": "unavailable_blinded"}
    assert all(asdict(arm) == saved for _, arm, saved in arms)


def test_blind_home_survives_repeat_and_pool_drift_without_rewriting_day_log(interleave_case):
    db, build, _ = interleave_case
    first = build(db_path=db, K=5, now=_DEFAULT_NOW)
    log = fetch_interleave_log(db)
    assert log
    again = build(db_path=db, K=5, now=_DEFAULT_NOW)
    assert asdict(again) == asdict(first) and fetch_interleave_log(db) == log
    _insert(db, item_key="NEW-MID", decision="awaiting_review", composite_score=3.5,
            corpus_affinity=0.2, goal_sims={"g": 0.9})
    drifted = build(db_path=db, K=5, now=_DEFAULT_NOW)
    assert any(p.item_key == "NEW-MID" for p in drifted.papers)
    assert drifted.selection_policy == "interleave" and fetch_interleave_log(db) == log
    assert all(p.selection_evidence == {"mode": "interleave", "basis": "unavailable_blinded"}
               for p in drifted.papers)


@pytest.mark.parametrize("quality_first,score,relevance,eligible", [
    (False, 1.9999, None, False), (False, 2.0, None, True),
    (False, 3.5, None, True), (False, 4.5, None, True),
    (True, 1.0, None, True), (True, 4.8, 2.0, False),
    (True, 1.0, 2.0001, True),
])
def test_model_floor_boundaries_do_not_become_discovery_gates(
    monkeypatch, quality_first, score, relevance, eligible,
):
    monkeypatch.setattr(allocator, "band_primary_enabled", lambda: False)
    cand = _cand(1, score=score, relevance=relevance, affinity=-0.1, surprise=0.9)
    for role in ("model", "surprise", "diversity"):
        papers, empty = allocator.allocate(
            candidate_pool=[cand], roles={role: 1}, K=1, quality_first=quality_first,
        )
        expected = eligible or role != "model"
        assert [p.item_id for p in papers] == ([1] if expected else [])
        assert empty == ([] if expected else ["model"])
        if papers:
            assert papers[0].selection_evidence["selection"]["requested_role"] == role


def test_empty_interleave_still_reports_common_root_policy_and_old_empty_state(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    _create_db(db)
    monkeypatch.setattr(deep_review, "current_reviews", lambda: {})
    monkeypatch.setattr(daily, "rank_interleave_enabled", lambda: True)
    monkeypatch.setattr(allocator, "band_primary_enabled", lambda: False)
    slate = daily.assemble_daily_slate(db_path=db, K=5, now=_DEFAULT_NOW)
    payload = _slate_to_payload(slate)
    assert payload["papers"] == [] and payload["pool_size"] == 0
    assert payload["selection_policy"] == slate.selection_policy == "interleave"
    assert payload["empty_role_events"] == ["model", "model", "model", "surprise", "diversity"]
    assert payload["fellback_to_recent"] is False
    assert payload["low_relevance_hidden"] == 0 and payload["weak_slate"] is False
    assert fetch_interleave_log(db) == []
