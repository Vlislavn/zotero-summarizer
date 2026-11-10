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
from zotero_summarizer.api.app import create_app
from zotero_summarizer.api.routes import research_feed as routes
from zotero_summarizer.services.research_feed import profile, telegram_job, telegram_live, telegram_today
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
    request(app, "POST", "/api/research-feed/telegram/collect-consent", {"allow": True})
    result = request(app, "POST", "/api/research-feed/telegram/runs")
    assert result.status_code == 409 and result.json()["error"] == "telegram_local_required"
    child.assert_not_called()


def _run(app):
    """Grant the one-time consent, tap once, wait for the background job; return (start response, final status)."""
    request(app, "POST", "/api/research-feed/telegram/collect-consent", {"allow": True})
    first = request(app, "POST", "/api/research-feed/telegram/runs")
    telegram_job._thread.join(10)
    return first, request(app, "GET", "/api/research-feed/telegram/status").json()


def _today(summary="commenters agree [c1]"):
    return {"schema_version": 1, "generated_at": "2026-11-07T12:00:00Z", "goals": ["agent checkpoints"],
            "posts": [{"source_id": "telegram:-100:10", "channel": "chan", "permalink": "https://t.me/chan/10",
                       "published_at": "2026-11-07T11:00:00+00:00", "text": "line one", "why_goal": "agent checkpoints",
                       "discussion": {"comment_count": 3, "label": "summary", "summary": summary, "top_comment_ids": ["c1"]}}],
            "counts": {"gated": 1}, "account": {},
            "trends": {"terms": [{"term": "agent eval", "comments": 6, "channels": 2}], "clusters": []}}


def test_action_collects_and_serves_today_digest_without_a_review_bundle(project, monkeypatch):
    settings, app = project
    assert request(app, "PUT", "/api/research-feed/telegram/sources", selection()).status_code == 200
    monkeypatch.setattr(telegram_live, "collect",
                        lambda s, profile, config: telegram_today.persist_today(s, _today()))
    action, state = _run(app)
    assert action.status_code == 202 and action.json()["state"] == "collecting"
    assert state["connection"] == "verified" and state["verified_at"] and state["state"] == "reviewed"
    today = request(app, "GET", "/api/research-feed/telegram/today")
    assert today.status_code == 200
    data = today.json()
    assert data["posts"][0]["discussion"]["summary"] == "commenters agree [c1]"
    assert data["posts"][0]["why_goal"] == "agent checkpoints"
    assert data["trends"]["terms"][0]["term"] == "agent eval"
    assert str(settings.data_dir) not in action.text + today.text


def test_action_failure_reports_error_without_leaking_secrets(project, monkeypatch):
    _settings, app = project
    request(app, "PUT", "/api/research-feed/telegram/sources", selection())
    monkeypatch.setattr(telegram_live, "collect", Mock(side_effect=RuntimeError("secret path /private/source")))
    _, value = _run(app)
    assert value["state"] == "error" and value["connection"] == "unknown"
    assert value["error"] == "telegram_action_failed"
    status = request(app, "GET", "/api/research-feed/telegram/status")
    assert "/private/source" not in status.text + request(app, "POST", "/api/research-feed/telegram/runs").text


@pytest.mark.parametrize("code", ["missing_session", "session_unauthorized", "revoked_session"])
def test_readiness_requires_actual_verification_and_auth_remains_outside_http(project, monkeypatch, code):
    _settings, app = project
    request(app, "PUT", "/api/research-feed/telegram/sources", selection())
    monkeypatch.setattr(telegram_live, "_require_model", lambda *args: None)
    monkeypatch.setattr(_telegram, "run_child", Mock(side_effect=_telegram.TelegramChildFailed(code)))
    _, value = _run(app)
    assert value["connection"] == "needs_auth" and value["verified_at"] is None
    assert value["error"] == code
    assert not any("auth" in route.path for route in app.routes if route.path.startswith('/api/research-feed'))
