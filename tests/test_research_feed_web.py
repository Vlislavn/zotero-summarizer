"""Read-only Weekly HTTP contracts over the existing publisher's real bundles."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from tests.test_research_feed import _review, _seed, _settings
from zotero_summarizer.api.app import create_app
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import research_feed as routes
from zotero_summarizer.services.research_feed import reports
from zotero_summarizer.services.research_feed.runner import run_weekly


def _request(app, path):
    async def send():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path)
    return asyncio.run(send())


@pytest.fixture
def saved_report(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed(settings, "feed:weekly-paper", "Agent harness benchmark", materialized="Z1")
    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    path = Path(result["json_path"])
    monkeypatch.setattr(routes, "settings", lambda: settings)
    return settings, create_app(settings), path


def test_weekly_http_lists_opens_and_downloads_existing_publication_without_writes(saved_report):
    settings, app, path = saved_report
    run_id = path.parent.name
    snapshots = {file: file.read_bytes() for file in settings.data_dir.rglob("*") if file.is_file()}

    listing = _request(app, "/api/research-feed/runs")
    detail = _request(app, f"/api/research-feed/runs/{run_id}")
    canonical = _request(app, f"/api/research-feed/runs/{run_id}/download?format=json")
    markdown = _request(app, f"/api/research-feed/runs/{run_id}/download?format=md")

    assert listing.status_code == detail.status_code == canonical.status_code == markdown.status_code == 200
    assert listing.json()["items"][0]["run_id"] == run_id
    assert listing.json()["items"][0]["available"] is True
    assert detail.json() == json.loads(path.read_text())
    assert detail.json()["cards"][0]["candidate"]["source_id"] == "feed:weekly-paper"
    assert detail.json()["cards"][0]["provenance"]["basis"] == "full_text"
    assert canonical.content == path.read_bytes()
    assert markdown.content == path.with_suffix(".md").read_bytes()
    for response in (canonical, markdown):
        assert "attachment" in response.headers["content-disposition"]
        assert str(settings.data_dir) not in response.text
    assert {file: file.read_bytes() for file in settings.data_dir.rglob("*") if file.is_file()} == snapshots
    assert not any(route.path == "/api/research-feed/run" for route in app.routes)


def test_weekly_http_empty_library_is_not_an_unreadable_report(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(routes, "settings", lambda: settings)
    app = create_app(settings)

    assert _request(app, "/api/research-feed/runs").json() == {"items": []}
    missing = _request(app, "/api/research-feed/runs/weekly-missing")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    assert not (settings.data_dir / "research_feed").exists()


def test_legacy_v1_optional_fields_remain_absent_and_artifacts_unchanged(saved_report):
    _, app, path = saved_report
    payload = json.loads(path.read_text())
    del payload["cards"][0]["provenance"]
    del payload["research_ideas_by_project"]
    payload["cards"][0]["card"]["worth_reading"] = "unknown"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    saved_bytes = path.read_bytes()

    detail = _request(app, f"/api/research-feed/runs/{path.parent.name}")

    assert detail.status_code == 200
    assert detail.json() == payload
    assert path.read_bytes() == saved_bytes


@pytest.mark.parametrize(("location", "bad_value"), [
    (("metadata", "schema_version"), True),
    (("metadata", "venue"), float("nan")),
    (("metadata", "venue"), float("inf")),
    (("metadata", "from"), "not a date"),
    (("metadata", "to"), None),
    (("metadata", "counts"), {}),
    (("metadata", "counts", "discovered"), True),
    (("metadata", "counts", "cards_generated"), -1),
    (("metadata", "counts", "cards_generated"), 2),
    (("cards",), []),
    (("cards",), {}),
    (("cards", 0, "candidate", "title"), {}),
    (("cards", 0, "card", "project_use_notes"), {"wrong": "shape"}),
    (("cards", 0, "card", "code_urls"), ["javascript:alert(1)"]),
    (("cards", 0, "provenance"), None),
    (("cards", 0, "provenance", "model"), []),
    (("failed",), [None]),
    (("manual_full_text_required",), [None]),
    (("rejected_near_threshold",), [None]),
    (("research_ideas_by_project",), None),
    (("research_ideas_by_project",), []),
    (("research_ideas_by_project",), {"agent harness": {}}),
    (("research_ideas_by_project",), {"agent harness": [""]}),
])
def test_malformed_nested_reports_are_unavailable_not_crashing_or_empty(saved_report, location, bad_value):
    _, app, path = saved_report
    payload = json.loads(path.read_text())
    parent = payload
    for key in location[:-1]:
        parent = parent[key]
    parent[location[-1]] = bad_value
    path.write_text(json.dumps(payload), encoding="utf-8")
    saved_bytes = path.read_bytes()
    base = f"/api/research-feed/runs/{path.parent.name}"

    detail = _request(app, base)
    listing = _request(app, "/api/research-feed/runs")

    assert detail.status_code == 422
    assert detail.json()["error"] == "report_unavailable"
    assert "cards" not in detail.json()
    assert listing.json()["items"][0]["available"] is False
    assert _request(app, base + "/download?format=json").status_code == 422
    assert _request(app, base + "/download?format=md").status_code == 422
    assert path.read_bytes() == saved_bytes


@pytest.mark.parametrize("raw", ["not JSON", "[]", "null", '{"metadata":', b"\xff", '{"extra": 1e999}'])
def test_invalid_json_or_encoding_is_visible(saved_report, raw):
    _, app, path = saved_report
    path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    response = _request(app, f"/api/research-feed/runs/{path.parent.name}")
    assert response.status_code == 422
    assert _request(app, "/api/research-feed/runs").json()["items"][0]["available"] is False


@pytest.mark.parametrize(("field", "value"), [("schema_version", 2), ("source", "telegram")])
def test_unsupported_report_is_distinct_from_invalid_report(saved_report, field, value):
    _, app, path = saved_report
    payload = json.loads(path.read_text())
    payload["metadata"][field] = value
    path.write_text(json.dumps(payload))
    base = f"/api/research-feed/runs/{path.parent.name}"

    for suffix in ("", "/download?format=json", "/download?format=md"):
        response = _request(app, base + suffix)
        assert response.status_code == 422
        assert response.json()["error"] == "report_unsupported"
    assert _request(app, "/api/research-feed/runs").json()["items"][0]["available"] is False


@pytest.mark.parametrize("scan_error", [PermissionError("denied"), OSError("I/O failed")])
def test_failed_directory_scan_is_unavailable_not_empty(saved_report, monkeypatch, scan_error):
    _, app, path = saved_report
    root = path.parent.parent
    scandir = os.scandir

    def scan(directory):
        if Path(directory) == root:
            raise scan_error
        return scandir(directory)

    monkeypatch.setattr(os, "scandir", scan)
    response = _request(app, "/api/research-feed/runs")

    assert response.status_code == 503
    assert response.json()["error"] == "reports_unavailable"
    assert "items" not in response.json()
    assert str(root) not in response.text


def test_valid_empty_digest_preserves_missing_reviews_rejects_and_failures(saved_report):
    _, app, path = saved_report
    payload = json.loads(path.read_text())
    record = payload["cards"].pop()
    payload["metadata"]["counts"]["cards_generated"] = 0
    payload["manual_full_text_required"] = [record["candidate"]]
    payload["rejected_near_threshold"] = [{"candidate": record["candidate"], "triage": record["triage"]}]
    payload["failed"] = [{"source_id": "feed:failed", "error": "review timed out"}]
    payload["research_ideas_by_project"] = {"agent harness": []}
    path.write_text(json.dumps(payload))

    response = _request(app, f"/api/research-feed/runs/{path.parent.name}")

    assert response.status_code == 200
    assert response.json() == payload


def test_symlinks_traversal_and_missing_markdown_do_not_escape_or_leak(saved_report, tmp_path):
    settings, app, path = saved_report
    root = path.parent.parent
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("not a weekly digest")
    (root / "weekly-link").symlink_to(tmp_path, target_is_directory=True)
    markdown = path.with_suffix(".md")
    markdown.unlink()
    base = f"/api/research-feed/runs/{path.parent.name}"
    assert _request(app, base).status_code == 200
    assert _request(app, base + "/download?format=md").status_code == 404
    markdown.symlink_to(unrelated)

    response = _request(app, base + "/download?format=md")
    assert response.status_code == 404
    assert unrelated.read_text() not in response.text
    assert _request(app, "/api/research-feed/runs/weekly-link").status_code == 404
    path.unlink()
    path.symlink_to(unrelated)
    assert _request(app, base).status_code == 404
    for run_id in ("../unrelated", "/tmp/weekly-secret", "weekly-a/b", "weekly-α"):
        with pytest.raises(APIError) as caught:
            reports.get_report(settings, run_id)
        assert caught.value.status_code == 404
        assert str(tmp_path) not in str(caught.value)
    assert _request(app, base + "/download?format=html").status_code == 422


def test_listing_bounds_isolates_damage_and_ignores_unpublished_or_unrelated_files(saved_report):
    _, app, path = saved_report
    root = path.parent.parent
    (root / ".weekly-staging").mkdir()
    (root / "profile.json").write_text("{}")
    (root / "weekly-zz-damaged").mkdir()
    listing = _request(app, "/api/research-feed/runs?limit=1")
    assert listing.json() == {"items": [{"run_id": "weekly-zz-damaged", "available": False}]}
    assert len(_request(app, "/api/research-feed/runs").json()["items"]) == 2
    for limit in (0, 101):
        assert _request(app, f"/api/research-feed/runs?limit={limit}").status_code == 422
