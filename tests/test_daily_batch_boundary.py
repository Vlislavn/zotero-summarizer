"""Add/Trash resolve a unique, valid batch before any side effect."""
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import daily
from zotero_summarizer.services.triage import daily_actions
from zotero_summarizer.services.library import review_materialize
from tests.test_daily_actions import env, _record  # noqa: F401 - shared isolated fixture


@pytest.fixture
def client():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(daily.router)
    with TestClient(app, raise_server_exceptions=False) as http:
        yield http


@pytest.mark.parametrize("action,counter", [("add-to-library", "added"), ("trash", "trashed")])
def test_duplicate_ids_have_one_effect_in_first_seen_order(env, monkeypatch, client, action, counter):
    db, labels = env
    first, second = _record(db, 101), _record(db, 202)
    writer = Mock()
    writer.apply_feed_materialization.side_effect = lambda **kw: {"item_key": kw["new_item_key"]}
    writer.mark_feed_items_read.side_effect = len
    monkeypatch.setattr(daily_actions, "ZoteroWriter", lambda _: writer)
    monkeypatch.setattr(review_materialize, "get_settings", daily_actions.get_settings)
    monkeypatch.setattr(daily_actions.deep_review, "copy_review", lambda *args: None)
    monkeypatch.setattr(daily_actions, "_attach_fulltext_best_effort", lambda _: {"attached": 0})
    monkeypatch.setattr(daily_actions, "_carry_renders_best_effort", lambda _: None)

    response = client.post(f"/api/daily/{action}", json={"item_ids": [second, first, second, first]})

    assert response.status_code == 200, response.text
    assert response.json()[counter] == 2
    assert response.json()["failed_count"] == 0
    assert [entry[0] for entry in labels] == [202, 101]
    if action == "add-to-library":
        assert writer.apply_feed_materialization.call_count == 2
        with sqlite3.connect(db) as conn:
            keys = [row[0] for row in conn.execute(
                "SELECT materialized_zotero_key FROM processed_feed_items ORDER BY id"
            )]
        assert all(keys) and len(set(keys)) == 2
    else:
        writer.mark_feed_items_read.assert_called_once_with([202, 101])


@pytest.mark.parametrize("action", ["add-to-library", "trash"])
@pytest.mark.parametrize("mixed", [False, True])
def test_missing_id_rejects_entire_batch_before_writes(env, monkeypatch, client, action, mixed):
    db, labels = env
    valid = _record(db, 101)
    missing = valid + 999
    writer = Mock(side_effect=AssertionError("writer opened before preflight"))
    monkeypatch.setattr(daily_actions, "_open_optional_writer", writer)

    response = client.post(f"/api/daily/{action}", json={"item_ids": [valid, missing] if mixed else [missing]})

    assert response.status_code == 404, response.text
    assert str(missing) in response.json()["message"]
    writer.assert_not_called()
    assert labels == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == 0
        row = conn.execute("SELECT decision, materialized_zotero_key FROM processed_feed_items").fetchone()
    assert row == ("triaged_pending", None)


@pytest.mark.parametrize("action", ["add-to-library", "trash"])
@pytest.mark.parametrize("ids", [[], [0], [-1], [True], [1.0], ["1"], [2**63]])
def test_invalid_http_ids_do_not_reach_service(monkeypatch, client, action, ids):
    loader = Mock(side_effect=AssertionError("invalid input reached service"))
    monkeypatch.setattr(daily_actions, "_load_rows", loader)
    response = client.post(f"/api/daily/{action}", json={"item_ids": ids})
    assert response.status_code == 422
    loader.assert_not_called()


@pytest.mark.parametrize("action", [daily_actions.add_to_library, daily_actions.trash])
@pytest.mark.parametrize("ids", [[], [True], [1.5], ["1"], [0], [2**63]])
def test_direct_call_validates_ids_before_io(monkeypatch, action, ids):
    settings = Mock(side_effect=AssertionError("I/O setup before validation"))
    monkeypatch.setattr(daily_actions, "get_settings", settings)
    with pytest.raises(APIError) as caught:
        action(ids)
    assert caught.value.status_code == 422
    settings.assert_not_called()
