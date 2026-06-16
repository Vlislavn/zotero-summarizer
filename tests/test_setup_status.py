"""Setup status: shape, the `ready` gating logic, and the SECURITY invariant that
NO secret VALUE ever appears in the response (only the api_key_env NAME + a bool).

GET /api/setup/status is the onboarding readiness probe.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from zotero_summarizer.models import AppState
from zotero_summarizer.runtime import AppContext, RuntimeState, set_context
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.setup import status as status_mod
from zotero_summarizer.services.setup import get_setup_status
from zotero_summarizer.settings import Settings


_SECRET = "sk-super-secret-DO-NOT-LEAK-12345"


class _Reader:
    def __init__(self, *, feeds: int):
        self._feeds = feeds

    def get_feed_groups(self):
        return [{"library_id": i} for i in range(self._feeds)]


def _write_valid_goals(config_path: Path) -> None:
    with open("goals.example.yaml", "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    raw["llm"]["api_base"] = "http://localhost:11434/v1"
    raw["llm"]["api_key_env"] = "ZS_TEST_KEY"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _seed(tmp_path: Path, *, key_value: str | None, db_found: bool, feeds: int) -> Settings:
    """Build a hermetic context: tmp project root, a valid goals.yaml, a stubbed
    Zotero reader + status payload, and a stubbed classifier card."""
    settings = Settings.load(project_root=tmp_path)
    _write_valid_goals(settings.config_path)

    state = RuntimeState()
    state.app_state = AppState(config=read_config(settings.config_path))
    state.zotero_reader = _Reader(feeds=feeds) if db_found else None
    set_context(AppContext(settings=settings, state=state))
    return settings


def _patch_externals(monkeypatch, *, key_value: str | None, db_found: bool, feeds: int):
    if key_value is None:
        monkeypatch.delenv("ZS_TEST_KEY", raising=False)
    else:
        monkeypatch.setenv("ZS_TEST_KEY", key_value)

    # Reachability + classifier are advisory; stub them so the test is offline.
    async def _reach():
        return {"status": "ok", "stages": [
            {"stage": "deep_review", "reachable": True, "detail": ""},
        ]}

    monkeypatch.setattr(status_mod.operational_check, "check_reachability", _reach)

    async def _card():
        return {"model": None}

    monkeypatch.setattr(status_mod, "model_card", _card)

    # Stub the Zotero status payload (avoid touching a real Zotero DB).
    def _payload():
        return SimpleNamespace(
            available=db_found,
            data_dir="/tmp/zot",
            db_path="/tmp/zot/zotero.sqlite",
            stats={"total_items": 42} if db_found else {},
            error="" if db_found else "Zotero database not found",
        )

    monkeypatch.setattr(status_mod, "zotero_status_payload", _payload)


def _run(coro):
    return asyncio.run(coro)


def test_status_shape_and_ready_true(tmp_path, monkeypatch):
    _seed(tmp_path, key_value=_SECRET, db_found=True, feeds=3)
    _patch_externals(monkeypatch, key_value=_SECRET, db_found=True, feeds=3)
    resp = _run(get_setup_status())

    assert resp.ready is True
    assert resp.config.present and resp.config.valid
    assert resp.config.research_goals_count > 0
    assert resp.llm.api_key_env == "ZS_TEST_KEY"
    assert resp.llm.api_key_present is True
    assert resp.zotero.db_found is True
    assert resp.zotero.feed_count == 3
    assert resp.zotero.library_item_count == 42


def test_no_secret_value_anywhere_in_response(tmp_path, monkeypatch):
    """The serialized response must contain the env-var NAME + the bool, and
    NEVER the key value. This is the load-bearing security assertion."""
    _seed(tmp_path, key_value=_SECRET, db_found=True, feeds=1)
    _patch_externals(monkeypatch, key_value=_SECRET, db_found=True, feeds=1)
    resp = _run(get_setup_status())

    blob = json.dumps(resp.model_dump())
    assert _SECRET not in blob          # the secret value never appears
    assert "ZS_TEST_KEY" in blob        # the env-var NAME does
    assert '"api_key_present": true' in blob


def test_ready_false_when_api_key_absent(tmp_path, monkeypatch):
    _seed(tmp_path, key_value=None, db_found=True, feeds=1)
    _patch_externals(monkeypatch, key_value=None, db_found=True, feeds=1)
    resp = _run(get_setup_status())
    assert resp.llm.api_key_present is False
    assert resp.ready is False  # key presence gates ready


def test_ready_false_when_zotero_db_missing(tmp_path, monkeypatch):
    _seed(tmp_path, key_value=_SECRET, db_found=False, feeds=0)
    _patch_externals(monkeypatch, key_value=_SECRET, db_found=False, feeds=0)
    resp = _run(get_setup_status())
    assert resp.zotero.db_found is False
    assert resp.ready is False  # db_found gates ready


def test_ready_false_when_config_invalid(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    settings.config_path.write_text("research_goals: []\n", encoding="utf-8")  # invalid
    state = RuntimeState()
    # No app_state config needed; the LLM section short-circuits on invalid config.
    state.zotero_reader = _Reader(feeds=1)
    set_context(AppContext(settings=settings, state=state))
    _patch_externals(monkeypatch, key_value=_SECRET, db_found=True, feeds=1)

    resp = _run(get_setup_status())
    assert resp.config.valid is False
    assert resp.config.error  # the parse/validation error is surfaced
    assert resp.ready is False
    # With an invalid config there is no routing to inspect.
    assert resp.llm.api_key_present is False
    assert resp.llm.default_provider is None
