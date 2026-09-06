import asyncio
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api import app as app_module, errors
from zotero_summarizer.api.routes.setup import router
from zotero_summarizer.runtime import RuntimeState
from zotero_summarizer.services import health, lifecycle
from zotero_summarizer.settings import Settings


def test_test_environment_never_loads_checkout_state(tmp_path):
    from zotero_summarizer.runtime import get_context

    context = get_context()
    assert context.settings.project_root == tmp_path.resolve()
    assert not context.settings.env_path.exists()
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="mock network"):
            sock.connect(("127.0.0.1", 11434))


def test_validation_never_echoes_secret_input(caplog):
    app = FastAPI()
    errors.install_error_handlers(app)
    app.include_router(router)
    secret = "SECRET_SENTINEL"
    response = TestClient(app).put("/api/setup/ai-credential", json={"name": "custom", "api_key": {"secret": secret}})
    assert response.status_code == 422
    assert secret not in response.text + caplog.text
    assert response.json()["details"]["errors"] == [{"loc": ["body", "api_key"], "type": "string_type"}]


@pytest.mark.parametrize("exception,status", [
    (FileNotFoundError, 404), (errors.ExtractionError, 422),
    (errors.LLMTimeoutError, 504), (errors.ZoteroReadError, 503),
    (errors.ZoteroWriteError, 503),
])
def test_integration_errors_do_not_disclose_paths_or_secrets(exception, status):
    app = FastAPI()
    errors.install_error_handlers(app)

    @app.get("/failure")
    def fail():
        raise exception("/Users/alice/private/secret.pdf API_KEY=secret")

    response = TestClient(app).get("/failure")
    assert response.status_code == status
    assert "secret" not in response.text and "/Users" not in response.text


def test_partial_spa_build_has_bounded_missing_artifacts(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", tmp_path)
    app = FastAPI()
    errors.install_error_handlers(app)
    app_module._install_spa(app)
    client = TestClient(app)
    assert client.get("/today").text == "<html>SPA</html>"
    for path in ("/api", "/api/missing", "/assets", "/assets/missing.js", "/sw.js", "/manifest.webmanifest", "/app-icon.svg"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"] == "application/json"
    (tmp_path / "sw.js").write_text("worker")
    assert client.get("/sw.js").text == "worker"


def test_health_before_startup(monkeypatch):
    monkeypatch.setattr(health, "state", RuntimeState)
    result = asyncio.run(health.health())
    assert result.status == "starting" and not result.config_loaded


def test_startup_rss_reads_project_env_after_import(tmp_path, monkeypatch):
    from zotero_summarizer.integrations.app_rss import AppRssReader

    (tmp_path / ".env").write_text("ZS_STARTUP_RSS_MAX_FEEDS=2\nZS_STARTUP_RSS_MAX_NEW_PER_FEED=3\nZS_STARTUP_RSS_TIMEOUT_SECS=1.5\n")
    for key in ("ZS_STARTUP_RSS_MAX_FEEDS", "ZS_STARTUP_RSS_MAX_NEW_PER_FEED", "ZS_STARTUP_RSS_TIMEOUT_SECS"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings.load(project_root=tmp_path)
    seen = []
    monkeypatch.setattr(AppRssReader, "refresh_feeds", lambda _, **kwargs: seen.append(kwargs))

    async def run():
        assert lifecycle._schedule_startup_rss_refresh(asyncio.get_running_loop(), settings)
        await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not asyncio.current_task()))
        monkeypatch.setenv("ZS_STARTUP_RSS_MAX_FEEDS", "0")
        assert not lifecycle._schedule_startup_rss_refresh(asyncio.get_running_loop(), settings)

    asyncio.run(run())
    assert seen == [{"max_feeds": 2, "max_new_items_per_feed": 3, "per_feed_timeout": 1.5}]
