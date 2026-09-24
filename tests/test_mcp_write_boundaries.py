"""Exercise public MCP tools through their real HTTP client, without live writes."""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from zotero_summarizer.mcp.tools import mutations, pending, search, triage


def run_tool(backend, tool, **kwargs):
    client = httpx.AsyncClient
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.MockTransport(backend), **kw,
    )):
        return asyncio.run(tool(**kwargs))


PATH_TOOLS = [
    (mutations.manage_tags, {"add_tags": ["read"]}, "item_key"),
    (mutations.manage_collections, {"add_collection_paths": ["Reading"]}, "item_key"),
    (mutations.set_reading_priority, {"priority": "must_read"}, "item_key"),
    (triage.submit_feedback, {"verdict": "approve"}, "item_key"),
    (triage.get_job_status, {}, "job_id"),
    (triage.cancel_job, {}, "job_id"),
    (search.get_paper, {}, "item_key"),
    (search.find_similar_papers, {"query": "paper"}, "item_key"),
]


@pytest.mark.parametrize("tool,kwargs,field", PATH_TOOLS)
@pytest.mark.parametrize("key", ["PAPER/../OTHER", "..", ".", "A\\B", "A\nB"])
def test_path_keys_cannot_redirect_tools(tool, kwargs, field, key):
    requests = []

    def backend(request):
        requests.append(request)
        return httpx.Response(404, json={"error": "not_found"})

    result = run_tool(backend, tool, **{**kwargs, field: key})
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"
    assert requests == []


@pytest.mark.parametrize("tool,kwargs,field", PATH_TOOLS)
def test_reserved_url_characters_remain_identifier_data(tool, kwargs, field):
    key = "A ?#%2Fé"
    requests = []

    def backend(request):
        requests.append(request)
        return httpx.Response(200, json={"active_items": [], "items": []})

    result = run_tool(backend, tool, **{**kwargs, field: key})
    assert result["ok"] is True
    first = requests[0].url
    assert first.query == b""
    assert first.fragment == ""
    assert key in first.path
    assert b"A%20%3F%23%252F%C3%A9" in first.raw_path
    if tool is search.get_paper:
        assert requests[1].url.path == f"/api/results/{key}"
        assert requests[-1].url.params["item_keys"] == key


@pytest.mark.parametrize("applied,failed", [(0, 1), (1, 1), (2, 0)])
def test_apply_reports_actual_write_outcome(applied, failed):
    ids = list(range(1, applied + failed + 1))
    writes = []

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [
                {"id": i, "change_type": "tag_changes"} for i in ids
            ]})
        writes.append(json.loads(request.content))
        return httpx.Response(200, json={
            "applied": applied, "failed": failed, "backup_path": "backup.sqlite",
            "failed_items": [{"id": i} for i in ids[applied:]],
        })

    result = run_tool(backend, pending.apply_pending_changes, change_ids=ids)
    assert result["ok"] is (failed == 0)
    summary = result if result["ok"] else result["error"]["details"]
    assert summary["applied"] == applied
    assert summary["failed"] == failed
    assert summary["backup_paths"] == ["backup.sqlite"]
    assert summary["requested_change_ids"] == ids
    assert writes == [{"change_ids": ids, "force": False}]
    if failed:
        assert result["error"]["retryable"] is False
        assert summary["failed_items"] == [{"id": i} for i in ids[applied:]]


@pytest.mark.parametrize("change_ids", [[], [0], [-1], [True], [1.5], ["1"]])
def test_explicit_invalid_or_empty_selection_never_expands_to_all(change_ids):
    requests = []

    def backend(request):
        requests.append(request)
        return httpx.Response(200, json={"count": 0})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=change_ids)
    assert requests == []
    assert result["ok"] is (change_ids == [])


@pytest.mark.parametrize("change_type", ["add_attachment", "set_field", "upsert_note", "future_write", ""])
def test_unreviewed_mutations_are_blocked(change_type):
    requests = []

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [{"id": 1, "change_type": change_type}]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1])
    assert result["ok"] is False
    assert result["error"]["code"] == "mcp_restricted"
    assert requests == ["GET"]


def test_missing_requested_rows_block_the_entire_write():
    requests = []

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [{"id": 1, "change_type": "tag_changes"}]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1, 9999])
    assert result["ok"] is False
    assert result["error"]["details"]["missing_change_ids"] == [9999]
    assert requests == ["GET"]


@pytest.mark.parametrize("last_result", ["failed", "force", "timeout"])
def test_later_chunk_failure_preserves_completed_writes(last_result):
    ids = list(range(1, 1002))
    writes = []

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": i, "change_type": "add_note"} for i in ids]})
        writes.append(json.loads(request.content))
        if len(writes) == 1:
            return httpx.Response(200, json={"applied": 1000, "failed": 0, "backup_path": "first.sqlite"})
        if last_result == "timeout":
            raise httpx.ReadTimeout("Response lost", request=request)
        if last_result == "force":
            return httpx.Response(200, json={"requires_force": True, "error": "zotero_running"})
        return httpx.Response(200, json={"applied": 0, "failed": 1, "failed_items": [{"id": 1001}], "backup_path": "last.sqlite"})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=ids)

    assert result["ok"] is False
    details = result["error"]["details"]
    assert details.get("applied", details.get("partial_applied")) == 1000
    assert details["backup_paths"][0] == "first.sqlite"
    assert result["error"]["retryable"] is False
    assert writes == [{"change_ids": ids[:1000], "force": False}, {"change_ids": ids[1000:], "force": False}]


def test_mcp_apply_matches_real_api_and_sqlite_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from zotero_summarizer.api.routes.pending import router
    from zotero_summarizer.services.zotero import zotero
    from zotero_summarizer.storage import repositories as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "triage.sqlite")
    db.init_db()
    db.insert_pending_changes("PAPER1", "Paper", [{"change_type": "add_note", "payload": {"note_html": "note"}}])
    change_id = db.get_pending_changes("pending", 10)[0]["id"]
    writes = []

    def failed_writer(changes, backup):
        assert backup is True
        writes.extend(row["id"] for row in changes)
        return {"applied_ids": [], "failed": [{"id": change_id, "error": "disk full"}], "backup_path": "copy.sqlite"}

    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: SimpleNamespace(
        is_connector_running=lambda: False, apply_changes=failed_writer,
    ))
    app = FastAPI()
    app.include_router(router)
    client = httpx.AsyncClient
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.ASGITransport(app=app), **kw,
    )):
        result = asyncio.run(pending.apply_pending_changes(change_ids=[change_id]))

    assert result["ok"] is False
    assert result["error"]["details"]["failed_items"] == [{"id": change_id, "error": "disk full"}]
    assert writes == [change_id]
    saved = db.get_pending_changes_by_ids([change_id])[0]
    assert saved["status"] == "failed"
    assert saved["error_message"] == "disk full"


def test_inbox_side_effect_failure_is_not_discarded():
    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": 1, "change_type": "tag_changes"}]})
        return httpx.Response(200, json={"applied": 1, "failed": 0, "inbox_removed_error": "locked"})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1])
    assert result["ok"] is False
    assert result["error"]["details"]["applied"] == 1
    assert result["error"]["details"]["inbox_removed_errors"] == ["locked"]


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_registered_mcp_tool_does_not_coerce_change_ids(value):
    from mcp.server.fastmcp.exceptions import ToolError

    from zotero_summarizer.mcp.server import mcp

    def backend(request):
        pytest.fail(f"Invalid protocol arguments must not reach HTTP: {request.url}")

    client = httpx.AsyncClient
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.MockTransport(backend), **kw,
    )):
        with pytest.raises(ToolError, match="validation error"):
            asyncio.run(mcp.call_tool("apply_pending_changes", {"change_ids": [value]}))


@pytest.mark.parametrize("data", [{}, {"applied": 1, "failed": -1}, {"applied": True, "failed": 0}])
def test_invalid_write_receipt_is_not_success(data):
    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": 1, "change_type": "add_note"}]})
        return httpx.Response(200, json=data)

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1])
    assert result["ok"] is False
    assert result["error"]["code"] == "backend_contract_error"
    assert result["error"]["details"]["unconfirmed_change_ids"] == [1]
