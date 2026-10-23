"""Weekly HTTP journey over real synthetic RSS, review projection and publication."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest

from tests.test_research_feed import _review, _seed, _settings
from zotero_summarizer.api.app import create_app
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import research_feed as routes
from zotero_summarizer.services.research_feed import reports, runner


def _request(app, method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(send())


def test_weekly_http_run_list_view_download_without_model_or_zotero(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent harness benchmark", materialized="Z1")
    monkeypatch.setattr(routes, "settings", lambda: settings)
    original = runner.run_weekly

    def cached_run(*args, **kwargs):
        assert kwargs["generate_reviews"] is False
        assert kwargs["dry_run"] is True and kwargs["queue_zotero"] is False
        return original(*args, **kwargs, review_loader=_review)

    monkeypatch.setattr(reports, "run_weekly", cached_run)
    app = create_app(settings)
    assert _request(app, "GET", "/api/research-feed/runs").json() == {"items": []}

    response = _request(app, "POST", "/api/research-feed/run", json={
        "start": "2026-08-20", "end": "2026-08-29", "card_budget": 5,
    })
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    listing = _request(app, "GET", "/api/research-feed/runs").json()["items"]
    assert listing[0]["run_id"] == run_id and listing[0]["available"] is True
    report = _request(app, "GET", f"/api/research-feed/runs/{run_id}").json()
    assert report["cards"][0]["candidate"]["title"] == "Agent harness benchmark"
    assert report["cards"][0]["provenance"]["basis"] == "full_text"
    assert report["metadata"]["dry_run"] is True
    assert report["metadata"]["to"].startswith("2026-08-29T23:59:59")
    for extension in ("json", "md"):
        download = _request(app, "GET", f"/api/research-feed/runs/{run_id}/download?format={extension}")
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
    assert "json_path" not in response.json()
    with sqlite3.connect(settings.triage_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pending_changes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM processed_feed_items").fetchone()[0] == 1


def test_weekly_http_empty_missing_cache_and_invalid_window(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent harness benchmark")
    monkeypatch.setattr(routes, "settings", lambda: settings)
    original = runner.run_weekly
    monkeypatch.setattr(reports, "run_weekly", lambda *a, **kw: original(
        *a, **kw, review_loader=lambda _key: None,
    ))
    app = create_app(settings)
    assert _request(app, "POST", "/api/research-feed/run", json={
        "start": "2026-08-30", "end": "2026-08-20",
    }).status_code == 422
    assert not (settings.data_dir / "research_feed").exists()
    response = _request(app, "POST", "/api/research-feed/run", json={
        "start": "2026-08-20", "end": "2026-08-29",
    })
    report = reports.get_report(settings, response.json()["run_id"])
    assert report["cards"] == []
    assert report["manual_full_text_required"] == []
    assert report["unqualified_reviews"][0]["candidate"]["source_id"] == "paper"
    assert report["unqualified_reviews"][0]["reason"] == "review_missing"
    for body in ({"card_budget": 11}, {"generate_reviews": True}, {"queue_zotero": True}):
        assert _request(app, "POST", "/api/research-feed/run", json={
            "start": "2026-08-20", "end": "2026-08-29", **body,
        }).status_code == 422


def test_saved_report_errors_are_visible_and_cannot_escape_selected_root(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    root = settings.data_dir / "research_feed"
    root.mkdir()
    damaged = root / "weekly-damaged"
    damaged.mkdir()
    (damaged / "weekly-damaged.json").write_text("not JSON")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "weekly-link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(routes, "settings", lambda: settings)
    app = create_app(settings)

    listing = _request(app, "GET", "/api/research-feed/runs").json()["items"]
    assert {row["run_id"] for row in listing if not row["available"]} == {"weekly-damaged", "weekly-link"}
    assert _request(app, "GET", "/api/research-feed/runs/weekly-damaged").status_code == 422
    assert _request(app, "GET", "/api/research-feed/runs/weekly-link").status_code == 404
    assert _request(app, "GET", "/api/research-feed/runs/weekly-missing").status_code == 404
    with pytest.raises(APIError) as caught:
        reports.get_report(settings, "../outside")
    assert caught.value.status_code == 404
    assert "outside" not in str(caught.value)


@pytest.mark.parametrize("bad_field", ["card", "provenance", "counts", "failed"])
def test_malformed_nested_report_is_an_error_not_a_crashing_card(tmp_path, monkeypatch, bad_field):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent harness benchmark")
    result = runner.run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    path = Path(result["json_path"])
    payload = json.loads(path.read_text())
    if bad_field == "card":
        payload["cards"][0]["card"]["project_use_notes"] = {"wrong": "shape"}
    elif bad_field == "provenance":
        payload["cards"][0]["provenance"] = None
    elif bad_field == "counts":
        payload["metadata"]["counts"]["discovered"] = {"wrong": "shape"}
    else:
        payload["failed"] = [None]
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(routes, "settings", lambda: settings)
    assert _request(create_app(settings), "GET", f"/api/research-feed/runs/{path.parent.name}").status_code == 422


@pytest.mark.parametrize(("idea_groups", "status_code"), [
    pytest.param({"agent harness": {}}, 422, id="object-group"),
    pytest.param({"agent harness": ""}, 422, id="empty-string-group"),
    pytest.param({"agent harness": None}, 422, id="null-group"),
    pytest.param({"agent harness": False}, 422, id="boolean-group"),
    pytest.param({"agent harness": 0}, 422, id="number-group"),
    pytest.param({"agent harness": [""]}, 422, id="invalid-idea-record"),
    pytest.param([], 422, id="array-instead-of-grouping-object"),
    pytest.param("", 422, id="string-instead-of-grouping-object"),
    pytest.param(None, 422, id="null-instead-of-grouping-object"),
    pytest.param({"agent harness": []}, 200, id="empty-list-group"),
    pytest.param({}, 200, id="empty-grouping-object"),
    pytest.param({"agent harness": [{
        "proposed_change": "Compare α with the current baseline — no automatic adoption.",
        "source_paper_id": "paper", "target_project": "agent harness",
        "minimal_validation_experiment": "Replay the same frozen synthetic inputs.",
    }], "activation oracle": []}, 200, id="populated-and-empty-list-groups"),
])
def test_saved_report_idea_group_shapes_control_detail_and_listing(
    tmp_path, monkeypatch, idea_groups, status_code,
):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent harness benchmark")
    result = runner.run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    path = Path(result["json_path"])
    payload = json.loads(path.read_text())
    payload["research_ideas_by_project"] = idea_groups
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    saved_bytes = path.read_bytes()
    monkeypatch.setattr(routes, "settings", lambda: settings)
    app = create_app(settings)

    response = _request(app, "GET", f"/api/research-feed/runs/{path.parent.name}")
    listing = _request(app, "GET", "/api/research-feed/runs")

    assert response.status_code == status_code
    assert listing.status_code == 200
    entry = next(row for row in listing.json()["items"] if row["run_id"] == path.parent.name)
    assert entry["available"] is (status_code == 200)
    if status_code == 200:
        assert response.json() == payload
    else:
        assert response.json()["error"] == "report_unavailable"
        assert "cards" not in response.json()
    assert path.read_bytes() == saved_bytes


def test_download_rejects_symlinked_file(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    root = settings.data_dir / "research_feed" / "weekly-synthetic"
    root.mkdir(parents=True)
    secret = tmp_path / "unrelated.txt"
    secret.write_text("not a report")
    (root / "weekly-synthetic.md").symlink_to(secret)
    monkeypatch.setattr(routes, "settings", lambda: settings)
    response = _request(create_app(settings), "GET", "/api/research-feed/runs/weekly-synthetic/download?format=md")
    assert response.status_code == 404
    assert secret.read_text() not in response.text
