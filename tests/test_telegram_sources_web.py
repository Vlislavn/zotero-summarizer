"""Existing Sources→bounded action→Weekly ownership, with synthetic source/model I/O."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from tests.test_research_feed_telegram import _project
from tests.test_telegram_conversations import snapshot, generated_response
from zotero_summarizer.api.app import create_app
from zotero_summarizer.api.routes import research_feed as routes
from zotero_summarizer.services.research_feed import telegram_conversations, profile
from zotero_summarizer.services.llm import factory
from zotero_summarizer.cli import _telegram


def request(app, method, path, data=None):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
                                     base_url="http://127.0.0.1") as client:
            return await client.request(method, path, json=data)
    return asyncio.run(send())


@pytest.fixture
def project(tmp_path, monkeypatch):
    settings = _project(tmp_path)
    raw = json.loads(settings.config_path.read_text())
    raw["llm_routing"]["providers"].append({"name": "local", "base_url": "http://127.0.0.1:11434/v1", "api_key_env": "LOCAL"})
    settings.config_path.write_text(json.dumps(raw))
    monkeypatch.setattr(routes, "settings", lambda: settings)
    return settings, create_app(settings)


def selection():
    return {"sources": [{"kind": "topic", "username": "https://t.me/group/50"}],
            "model": {"provider": "local", "model": "synthetic"}, "limits": {"context_tokens": 32768}}


def test_explicit_source_save_is_narrow_read_only_and_never_guesses_connection(project, monkeypatch):
    settings, app = project
    raw = json.loads(profile.profile_path(settings.data_dir).read_text())
    raw["unrelated_extension"] = {"keep": ["verbatim"]}
    profile.profile_path(settings.data_dir).write_text(json.dumps(raw))
    child, model = Mock(side_effect=AssertionError("no activation")), Mock(side_effect=AssertionError("no model"))
    monkeypatch.setattr(_telegram, "run_child", child)
    monkeypatch.setattr(factory, "build_client_for_provider", model)
    before = {file: file.read_bytes() for file in settings.data_dir.rglob('*') if file.is_file()}
    read = request(app, "GET", "/api/research-feed/telegram/sources")
    status = request(app, "GET", "/api/research-feed/telegram/status")
    assert read.status_code == status.status_code == 200
    assert status.json()["connection"] == "unknown"
    assert {file: file.read_bytes() for file in settings.data_dir.rglob('*') if file.is_file()} == before
    saved = request(app, "PUT", "/api/research-feed/telegram/sources", selection())
    assert saved.status_code == 200
    assert saved.json()["sources"][0] == {"kind": "topic", "username": "group", "message_id": 50}
    after = json.loads(profile.profile_path(settings.data_dir).read_text())
    for key in ["themes", "projects", "topic_taxonomy", "unrelated_extension"]:
        if key in raw:
            assert after[key] == raw[key]
    assert child.call_count == model.call_count == 0
    assert str(settings.data_dir) not in saved.text
    assert "session" not in saved.text and "api_key" not in saved.text
    assert request(app, "POST", "/api/research-feed/telegram/runs", {"consent": False}).status_code == 409


def test_remote_explicit_selection_refuses_before_collection_or_client(project, monkeypatch):
    _settings, app = project
    value = selection(); value["model"]["provider"] = "news-default"
    assert request(app, "PUT", "/api/research-feed/telegram/sources", value).status_code == 200
    child = Mock(side_effect=AssertionError("must not collect"))
    monkeypatch.setattr(_telegram, "run_child", child)
    result = request(app, "POST", "/api/research-feed/telegram/runs", {"consent": True})
    assert result.status_code == 409 and result.json()["error"] == "telegram_local_required"
    child.assert_not_called()


def test_action_verifies_then_publishes_readable_source_bound_weekly_without_mutating_history(project, monkeypatch):
    settings, app = project
    assert request(app, "PUT", "/api/research-feed/telegram/sources", selection()).status_code == 200
    current = [snapshot()]
    def child(selected, purpose):
        assert selected == settings and purpose == "conversations"
        result = {"status": "collected", "cleanup": "completed", "account": {"status": "not_deferred", "retry_at": None},
                  "collection": {"authorized": True, "conversations": [current[0].model_dump()], "counts": {"messages": 1}}}
        exported = telegram_conversations.publish(settings, current, collection=result,
                                                   profile=profile.load_profile(settings.data_dir))
        return {"result": result, "export": exported}
    monkeypatch.setattr(_telegram, "run_child", child)
    client = Mock(); client.pydantic_prompt.return_value = generated_response()
    build = Mock(return_value=client); monkeypatch.setattr(factory, "build_client_for_provider", build)
    action = request(app, "POST", "/api/research-feed/telegram/runs", {"consent": True})
    assert action.status_code == 200
    state = action.json()
    assert state["connection"] == "verified" and state["verified_at"]
    assert state["state"] == "reviewed" and state["report_id"].startswith("telegram-review-")
    run = state["report_id"]
    detail = request(app, "GET", "/api/research-feed/runs/" + run)
    assert detail.status_code == 200
    assert detail.json()["topics"][0]["title"] == "Claim evaluation"
    assert detail.json()["metadata"]["evaluation_identity"]["local_only"] is True
    assert build.call_args.kwargs["local_only"] is True
    assert "https://t.me/group/51" in detail.text
    original = request(app, "GET", f"/api/research-feed/runs/{run}/download?format=json").content
    markdown = request(app, "GET", f"/api/research-feed/runs/{run}/download?format=md")
    assert markdown.status_code == 200 and "participant claim" in markdown.text
    assert str(settings.data_dir) not in detail.text + action.text + markdown.text
    listing = request(app, "GET", "/api/research-feed/runs").json()
    assert any(item["run_id"] == run and item["source"] == "telegram" and item["available"] for item in listing["items"])
    files = {file: file.read_bytes() for file in settings.data_dir.rglob('*') if file.is_file()}
    request(app, "GET", "/api/research-feed/telegram/status")
    request(app, "GET", "/api/research-feed/runs/" + run)
    assert {file: file.read_bytes() for file in settings.data_dir.rglob('*') if file.is_file()} == files
    assert client.pydantic_prompt.call_count == 1
    current[0] = snapshot("Edited participant claim")
    repeat = request(app, "POST", "/api/research-feed/telegram/runs", {"consent": True})
    assert repeat.json()["report_id"] != run
    assert client.pydantic_prompt.call_count == 2
    assert request(app, "GET", f"/api/research-feed/runs/{run}/download?format=json").content == original


def test_model_failure_preserves_collected_originals_and_reports_error_not_empty_success(project, monkeypatch):
    settings, app = project
    request(app, "PUT", "/api/research-feed/telegram/sources", selection())
    result = {"status": "collected", "cleanup": "completed", "account": {},
              "collection": {"authorized": True, "conversations": [snapshot().model_dump()]}}
    monkeypatch.setattr(_telegram, "run_child", lambda *_: {"result": result, "export": {"run_id": "telegram-review-collection"}})
    monkeypatch.setattr(factory, "build_client_for_provider", Mock(side_effect=RuntimeError("secret path /private/source")))
    value = request(app, "POST", "/api/research-feed/telegram/runs", {"consent": True}).json()
    assert value["state"] == "error" and value["connection"] == "verified"
    detail = request(app, "GET", "/api/research-feed/runs/" + value["report_id"])
    assert detail.status_code == 200
    assert detail.json()["topics"] == [] and detail.json()["errors"][0]["code"] == "review_failed"
    assert "participant claim" in detail.text and "/private/source" not in detail.text


def test_existing_v1_saved_news_is_readable_and_downloaded_verbatim_through_weekly(project, tmp_path):
    from tests.test_research_feed_telegram import _posts, _write_inputs
    from zotero_summarizer.services.research_feed.telegram import run_saved
    settings, app = project
    inputs = tmp_path / "originals.jsonl"
    _write_inputs(inputs, _posts())
    result = run_saved(settings, input_jsonl=inputs, collect_only=True)
    path = Path(result["json_path"])
    before = path.read_bytes()
    run = path.parent.name
    detail = request(app, "GET", "/api/research-feed/runs/" + run)
    download = request(app, "GET", f"/api/research-feed/runs/{run}/download?format=json")
    assert detail.status_code == download.status_code == 200
    assert detail.json()["metadata"]["schema_version"] == 1
    assert download.content == before == path.read_bytes()


@pytest.mark.parametrize("code", ["missing_session", "session_unauthorized", "revoked_session"])
def test_readiness_requires_actual_verification_and_auth_remains_outside_http(project, monkeypatch, code):
    _settings, app = project
    request(app, "PUT", "/api/research-feed/telegram/sources", selection())
    monkeypatch.setattr(_telegram, "run_child", Mock(side_effect=_telegram.TelegramChildFailed(code)))
    value = request(app, "POST", "/api/research-feed/telegram/runs", {"consent": True}).json()
    assert value["connection"] == "needs_auth" and value["verified_at"] is None
    assert value["error"] == code
    assert not any("auth" in route.path for route in app.routes if route.path.startswith('/api/research-feed'))
