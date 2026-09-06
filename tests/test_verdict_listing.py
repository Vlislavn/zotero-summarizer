"""A061: complete verdict snapshots for HTTP and the one-time transfer."""

import argparse
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import golden
from zotero_summarizer.cli import _goldenset_migrate
from zotero_summarizer.integrations import zotero_read, zotero_write
from zotero_summarizer.storage import repositories


def _seed(path):
    rows = [("OLDEST01", "must_read", "auto_quality"),
            ("OLDEST02", "dont_read", "auto_quality")]
    rows.extend((f"K{i:07d}", "dont_read", "user") for i in range(5000))
    with sqlite3.connect(path) as conn:
        conn.execute(repositories._CREATE_LABEL_VERDICTS_TABLE)
        conn.executemany(
            "INSERT INTO label_verdicts(item_key, user_priority, source, "
            "original_derived_priority, comment, created_at) "
            "VALUES (?, ?, ?, 'could_read', 'retained rationale', '2026-09-01 12:00:00')",
            rows,
        )
    return list(reversed(rows))


@pytest.mark.parametrize("query", [
    {}, {"source": "auto_quality"}, {"user_priority": "dont_read"},
    {"source": "auto_quality", "user_priority": "must_read"},
    {"source": "auto_quality", "user_priority": "dont_read"},
    {"source": "unknown"},
])
def test_http_lists_every_matching_verdict_in_order(tmp_path, monkeypatch, query):
    path = tmp_path / "triage.db"
    rows = _seed(path)
    monkeypatch.setattr(golden, "_db_path", lambda: path)
    app = FastAPI()
    app.include_router(golden.router)
    install_error_handlers(app)
    expected = [key for key, priority, source in rows
                if ("source" not in query or query["source"] == source)
                and ("user_priority" not in query or query["user_priority"] == priority)]

    with TestClient(app) as client:
        response = client.get("/api/golden/verdicts", params=query)
        invalid = client.get("/api/golden/verdicts", params={"user_priority": "invalid"})

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == len(expected)
    assert [row["item_key"] for row in result["verdicts"]] == expected
    assert all(row["comment"] == "retained rationale" for row in result["verdicts"])
    assert invalid.status_code == 422


@pytest.mark.parametrize("dry_run", [True, False])
def test_transfer_includes_verdicts_older_than_five_thousand(tmp_path, monkeypatch, capsys, dry_run):
    path = tmp_path / "triage.db"
    rows = _seed(path)
    settings = SimpleNamespace(triage_db_path=path, zotero_data_dir=tmp_path)
    monkeypatch.setattr(_goldenset_migrate.Settings, "load", lambda **kw: settings)
    monkeypatch.setattr(zotero_read, "ZoteroReader", lambda *args: SimpleNamespace(
        get_item_detail=lambda key: {"tags": ["topic:x"]},
    ))
    delivered = []

    def apply(changes, backup):
        assert backup is True
        delivered.extend(changes)
        return {"failed": [], "backup_path": "test-backup"}

    def writer(*args):
        assert not dry_run, "dry-run must not construct a writer"
        return SimpleNamespace(is_connector_running=lambda: False, apply_changes=apply)

    monkeypatch.setattr(zotero_write, "ZoteroWriter", writer)
    parser = argparse.ArgumentParser()
    _goldenset_migrate.register_goldenset_migrate(parser.add_subparsers())
    args = parser.parse_args(["migrate-verdicts-to-zotero"] + (["--dry-run"] if dry_run else []))

    assert args.func(args) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["verdicts_total"] == result["to_write"] == len(rows)
    assert [row["item_key"] for row in result["planned"]] == [row[0] for row in rows]
    assert result["planned"][-1]["add_tags"] == ["label:must_read"]
    assert len(delivered) == (0 if dry_run else len(rows))
