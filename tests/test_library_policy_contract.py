"""Library list limits and prestige bands agree at the HTTP/client boundary."""
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._reading_queue_support import FakeGate, FakeReader, isolate, item, patch_state, seed
from zotero_summarizer.api.routes import library
from zotero_summarizer.domain import apply_prestige_floor, score_to_priority
from zotero_summarizer.services.library import reading_queue


@pytest.mark.parametrize("include_read", [False, True])
@pytest.mark.parametrize("limit", [1, 2, 3])
def test_http_limit_caps_combined_unread_and_read(monkeypatch, tmp_path, include_read, limit):
    isolate(monkeypatch, tmp_path)
    patch_state(monkeypatch, FakeReader([item("A"), item("B"), item("R", tags=["🧠"])]), FakeGate("gate"))
    seed("gate", A=4.0, B=3.0, R=5.0)
    app = FastAPI()
    app.include_router(library.router)

    with TestClient(app) as client:
        response = client.get("/api/library/reading-queue", params={"include_read": include_read, "limit": limit})

    assert response.status_code == 200
    data = response.json()
    expected = ["A", "B", "R"] if include_read else ["A", "B"]
    assert [row["item_key"] for row in data["items"]] == expected[:limit]
    assert data["total_unread"] == 2
    assert data["read_hidden"] == 1
    assert data["distribution"]["total_scored"] == 2


@pytest.mark.parametrize("pairs,expected", [
    ([(0.1, True), (0.9, True)], 0.5),
    ([(0.1, True), (0.4, True), (0.6, True), (0.9, True)], 0.5),
    ([(0.1, True), (0.9, True), (0.8, False), (None, True)], 0.5),
])
def test_even_prestige_floor_is_arithmetic_median(pairs, expected):
    assert reading_queue.prestige_floor(pairs) == pytest.approx(expected)


def test_client_filter_and_fleet_match_server_floor_policy():
    cases = []
    for score in [None, 1.0, 2.0, 3.49, 3.5, 4.49, 4.5, 5.0]:
        for prestige in [None, 0.1, 0.5, 0.9]:
            for known in [False, True]:
                for floor in [None, 0.0, 0.5]:
                    row = dict(item_key=str(len(cases)), relevance_score=score,
                               prestige_score=prestige, prestige_known=known)
                    band = None if score is None else apply_prestige_floor(
                        score_to_priority(score), prestige, prestige_known=known, floor=floor,
                    )
                    cases.append({"row": row, "floor": floor, "band": band})
    script = '''
import {readFileSync} from 'node:fs';
import {BANDS, EMPTY_FILTERS, buildPredicate, coolUndecidedKeys} from './frontend/src/utils/relevanceBands.js';
const cases = JSON.parse(readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(({row, floor}) => ({
  bands: BANDS.filter(band => buildPredicate({...EMPTY_FILTERS, bands: [band]}, {prestigeFloor: floor})(row)),
  cool: coolUndecidedKeys([row], floor),
}))));
'''

    result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(cases),
                            text=True, capture_output=True, check=True, timeout=20,
                            cwd=Path(__file__).resolve().parents[1])

    for case, actual in zip(cases, json.loads(result.stdout), strict=True):
        assert actual["bands"] == ([case["band"]] if case["band"] is not None else []), case
        assert actual["cool"] == ([case["row"]["item_key"]] if case["band"] in {"must_read", "should_read"} else []), case
