"""Per-part review feedback: schema step 20, the text each part showed, withdrawal, review versions."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, api_error_handler
from zotero_summarizer.api.routes import review_feedback as routes
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.library import _review_cache, deep_review, review_feedback as service
from zotero_summarizer.services.library import review_feedback_bundle as bundle
from zotero_summarizer.services.library import review_feedback_snapshot as snapshot
from zotero_summarizer.storage import migrations, repositories

KEY, AT = "ABCD2345", "2026-11-11T15:02:36Z"


def _review():
    """Every data key of every part holds a distinct string, so a part's text can be checked against them."""
    digest = {name: f"digest {name} text" for name in (
        "read_decision", "read_why", "tldr", "verdict", "grade", "key_strength", "key_weakness", "original_value",
        "impact", "industry_impact", "academy_impact", "unknown_unknowns", "controversies", "methods", "limitations")}
    digest |= {"estimated_read_minutes": 17, "read_parts": ["read part one"], "skip_parts": ["skip part one"],
               "key_findings": ["finding one", "finding two"], "implementation": ["implement step"],
               "parameters": {"metrics": ["metric 91.5%"], "dataset": "dataset name", "architecture": "arch name"}}
    quality = {"coverage_met": 6, "coverage_applicable": 8, "quality_band": "flag", "missing_critical": ["no_code"],
               "rubric": {"baselines": "no"}, "evidence": {"baselines": "quote text"}, "red_flags": ["flag one", "flag two"],
               "overstatements": ["overstated claim"]}
    return {"reviewed_at": AT, "digest": digest, "quality": quality, "goal_summaries": [{"goal": "goal text", "summary": "goal summary"}],
            "code_link": {"found": True, "url": "https://example.test/repo"},
            "review_identity": {"generation_sha256": "ab" * 32, "source_kind": "none", "source_path": "", "source_sha256": ""},
            "provenance": {"model": "model-x"}, "review_freshness": {"status": "current", "reason": ""}}


@pytest.fixture
def review(monkeypatch):
    current = {"value": _review()}
    monkeypatch.setattr(deep_review, "get_usable_review", lambda key: current["value"] if key == KEY else None)
    monkeypatch.setattr(_review_cache, "_read_all", lambda **_: {KEY: {"stored": True}})
    monkeypatch.setattr(bundle, "_article", lambda key, previous: {"title": "A paper", "doi": "", "url": ""})
    monkeypatch.setattr(snapshot, "_spawn", lambda work: work())  # the background snapshot runs inline
    migrations.run_migrations(settings().triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
    return current


def _rows():
    with closing(repositories._connect_to(settings().triage_db_path)) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM review_feedback")]


def test_migration_20_keeps_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "triage.db"
    migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS[:19])
    with closing(repositories._connect_to(path)) as conn, conn:
        conn.execute("INSERT INTO review_feedback (item_key, reviewed_at, chips_json, note) VALUES ('K', 'T', '[\"too_long\"]', 'n')")
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == 20
    first = _dump(path)
    assert first == [("K", "T", '["too_long"]', "n", "{}")]
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == 20
    assert _dump(path) == first
    with closing(sqlite3.connect(path)) as conn:
        assert [r[1] for r in conn.execute("PRAGMA table_info(review_feedback)")].count("fields_json") == 1


def _dump(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT item_key, reviewed_at, chips_json, note, fields_json FROM review_feedback").fetchall()


def _leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    elif value not in (None, ""):
        yield str(value)


@pytest.mark.parametrize("field", list(bundle.PARTS))
def test_shown_text_carries_the_value_of_every_key_of_the_part(review, field):
    shown = bundle.render_shown(field, review["value"])
    for dotted, _label in bundle.PARTS[field][1]:
        node = review["value"]
        for name in dotted.split("."):
            node = node[name]
        assert all(leaf in shown for leaf in _leaves(node)), (field, dotted)
    saved = service.put_part(KEY, field, AT, ["flags_noise"], "")
    assert saved["feedback"]["fields"][field]["shown"] == shown
    pkg = settings().data_dir / saved["package"]["path"]
    feedback = json.loads((pkg / "feedback.json").read_text())
    assert feedback["parts"][field]["shown"] == shown
    assert json.loads((pkg / "review.json").read_text())["shown"]["digest"] == review["value"]["digest"]


def test_row_is_deleted_only_when_chips_note_and_parts_are_all_empty(review):
    service.put_part(KEY, "whole", AT, ["too_long"], "")
    service.put_part(KEY, "quality", AT, [], "grade looks high")
    service.delete_part(KEY, "whole", AT)
    (row,) = _rows()
    assert (row["chips_json"], row["note"]) == ("[]", "")  # the parts keep the row alive
    service.delete_part(KEY, "quality", AT)
    (row,) = _rows()
    assert json.loads(row["fields_json"])["quality"]["withdrawn"] is True  # tombstone: feedback.json still shows it
    service.delete_part(KEY, "whole", None)  # nothing left to withdraw: no change
    assert len(_rows()) == 1
    with closing(repositories._connect_to(settings().triage_db_path)) as conn, conn:
        conn.execute("UPDATE review_feedback SET fields_json = '[]'")  # first-draft default reads as empty
    service.put_part(KEY, "whole", AT, ["good_as_is"], "")
    service.delete_part(KEY, "whole", None)
    assert _rows() == []


def test_a_regenerated_review_is_409_and_unknown_things_are_422(review):
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)
    app.include_router(routes.router)
    client = TestClient(app)
    url = f"/api/library/review-feedback/{KEY}/parts/red_flags"
    body = {"reviewed_at": AT, "chips": ["flags_noise"], "note": "noise"}
    assert client.put(url, json=body).status_code == 200
    stale = client.put(url, json=body | {"reviewed_at": "2026-12-01T00:00:00Z"})
    assert (stale.status_code, stale.json()["error"]) == (409, "review_version_changed")
    assert client.delete(url, params={"reviewed_at": "old"}).status_code == 409
    assert client.put(url.replace("red_flags", "nonsense"), json=body).json()["error"] == "unknown_field"
    assert client.put(url, json=body | {"chips": ["bogus"]}).json()["error"] == "review_feedback_chip_unknown"
    assert client.put(url, json=body | {"chips": [], "note": " "}).status_code == 422
    assert client.put(url.replace(KEY, "NOPE0000"), json=body).json()["error"] == "review_not_found"
    assert client.delete(url, params={"reviewed_at": AT}).json()["cleared"] is True
    assert client.delete(url, params={"reviewed_at": AT}).json()["cleared"] is True  # idempotent
    assert len(_rows()) == 1
