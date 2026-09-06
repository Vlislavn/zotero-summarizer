"""Quality may change identities inside category slots, not the slot sequence."""
from copy import deepcopy
from itertools import product

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._daily_select_helpers import _create_db, _insert, _DEFAULT_NOW
from tests._reading_queue_support import FakeGate, FakeReader, isolate, item, patch_state, seed
from zotero_summarizer.api.routes import library
from zotero_summarizer.domain import score_to_priority
from zotero_summarizer.services.library import _ranking, deep_review
from zotero_summarizer.services.model.rank_blend import blend_scores, order_within_bands
from zotero_summarizer.services.triage.daily_select import assemble_daily_slate


@pytest.fixture(params=[False, True])
def quality_mode(request, monkeypatch):
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", str(int(request.param)))
    reviews = {"BELOW": {"quality": {"grade": "A", "quality_band": "highlight"}},
               "AT": {"quality": {"grade": "D", "quality_band": "flag"}}}
    monkeypatch.setattr(deep_review, "_read_all", lambda: reviews)


@pytest.mark.parametrize("edge", [2.0, 3.5, 4.5])
def test_http_quality_cannot_take_a_neighboring_category_slot(monkeypatch, tmp_path, quality_mode, edge):
    isolate(monkeypatch, tmp_path)
    patch_state(monkeypatch, FakeReader([item(k) for k in ["HI", "AT", "BELOW", "LO"]]), FakeGate("gate"))
    seed("gate", HI=5.0, AT=edge, BELOW=edge - 0.01, LO=1.0)
    app = FastAPI()
    app.include_router(library.router)

    with TestClient(app) as client:
        response = client.get("/api/library/reading-queue", params={"limit": 2})

    assert response.status_code == 200
    data = response.json()
    assert [r["item_key"] for r in data["items"]] == ["HI", "AT"]
    assert data["distribution"]["total_scored"] == 4
    assert data["items"][1]["relevance_score"] == edge


@pytest.mark.parametrize("edge", [3.5, 4.5])
@pytest.mark.parametrize("role", ["model", "diversity"])
def test_daily_model_role_keeps_category_slots(tmp_path, quality_mode, edge, role):
    db = tmp_path / "triage.db"
    _create_db(db)
    for key, score in [("HI", 5.0), ("AT", edge), ("BELOW", edge - 0.01), ("LO", 1.0)]:
        _insert(db, item_key=key, title=key, decision="awaiting_review", composite_score=score,
                materialized_zotero_key=key)

    slate = assemble_daily_slate(db_path=db, K=3, roles={role: 3}, now=_DEFAULT_NOW, quality_first=False)

    assert [p.title for p in slate.papers] == ["HI", "AT", "BELOW"]
    assert {p.role for p in slate.papers} == {"model" if role == "model" else "model_fallback"}


def test_goal_blend_slot_sequence_survives_all_quality_assignments(monkeypatch):
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "0")
    scores = [5.0, 3.50, 3.49, 2.5, 1.0]
    goals = [0.0, 0.45, 0.9, 0.1, 0.0]
    base = blend_scores(scores, goals, [None] * 5)
    expected_bands = [score_to_priority(scores[i]) for i in sorted(range(5), key=base.__getitem__, reverse=True)]
    assert expected_bands != [score_to_priority(score) for score in scores]

    for grades in product(["A", "D"], repeat=5):
        rows = [{"item_key": str(i), "relevance_score": score, "goal_sim": goals[i],
                 "date_added": "2026-09-06", "quality_grade": grades[i]} for i, score in enumerate(scores)]
        before = deepcopy(rows)
        _ranking._blended_sort(rows)
        assert [score_to_priority(r["relevance_score"]) for r in rows] == expected_bands, grades
        assert sorted(rows, key=lambda r: r["item_key"]) == before


def test_quality_still_reorders_close_scores_inside_a_band(monkeypatch):
    monkeypatch.setenv("ZS_QUALITY_BAND_PRIMARY", "0")
    rows = [{"item_key": key, "relevance_score": rel, "goal_sim": None,
             "date_added": "2026-09-06", "quality_grade": grade}
            for key, rel, grade in [("hi", 5, None), ("weak", 3.4, "D"),
                                    ("strong", 3.39, "A"), ("lo", 1, None)]]

    _ranking._blended_sort(rows)

    assert [r["item_key"] for r in rows] == ["hi", "strong", "weak", "lo"]


@pytest.mark.parametrize("bands,keys,expected", [
    ([], [], []),
    (["a"], [(1, "")], [0]),
    (["a", "b", "a"], [(1, ""), (3, ""), (2, "")], [2, 1, 0]),
    (["a", "a"], [(1, ""), (1, "")], [0, 1]),
    (["a", "a"], [(1, "old"), (1, "recent")], [1, 0]),
])
def test_band_slots_preserve_interleaving_and_ties(bands, keys, expected):
    assert order_within_bands(bands, keys) == expected


def test_band_slots_reject_misaligned_inputs():
    with pytest.raises(ValueError, match="parallel lists"):
        order_within_bands(["a"], [])
