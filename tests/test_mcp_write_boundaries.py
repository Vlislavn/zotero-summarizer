"""Exercise public MCP tools through their real HTTP client, without live writes."""
import asyncio
from copy import deepcopy
import hashlib
import json
import sqlite3
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


def presented_row(change_id=1, change_type="tag_changes", payload=None):
    payload = payload if payload is not None else {"add_tags": ["read"]}
    preview = {
        "version": 1, "change_id": change_id, "item_key": f"PAPER{change_id}",
        "change_type": change_type, "payload": payload, "edit_revision": 0,
        "target": {"database_path": "/owned/zotero.sqlite", "device": 1, "inode": 2,
                   "library_id": 1, "library_type": "user"},
        "generated_preview_sha256": None,
    }
    preview["preview_sha256"] = hashlib.sha256(json.dumps(
        preview, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return {"id": change_id, "item_key": preview["item_key"], "change_type": change_type,
            "payload_json": json.dumps(payload), "approval_preview": preview, "status": "pending"}


def approval_map(rows):
    return {str(row["id"]): row["approval_preview"]["preview_sha256"] for row in rows}


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
    rows = [presented_row(i) for i in ids]
    previews = approval_map(rows)
    writes = []

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": rows})
        writes.append(json.loads(request.content))
        return httpx.Response(200, json={
            "applied": applied, "failed": failed, "backup_path": "backup.sqlite",
            "failed_items": [{"id": i} for i in ids[applied:]],
        })

    result = run_tool(backend, pending.apply_pending_changes, change_ids=ids, previews=previews)
    assert result["ok"] is (failed == 0)
    summary = result if result["ok"] else result["error"]["details"]
    assert summary["applied"] == applied
    assert summary["failed"] == failed
    assert summary["backup_paths"] == ["backup.sqlite"]
    assert summary["requested_change_ids"] == ids
    assert writes == [{"change_ids": ids, "previews": previews, "force": False}]
    if failed:
        assert result["error"]["retryable"] is False
        assert summary["failed_items"] == [{"id": i} for i in ids[applied:]]


@pytest.mark.parametrize("change_ids", [[], [0], [-1], [True], [1.5], ["1"], None])
def test_explicit_invalid_or_empty_selection_never_expands_to_all(change_ids):
    requests = []

    def backend(request):
        requests.append(request)
        return httpx.Response(200, json={"count": 0})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=change_ids, previews={})
    assert requests == []
    assert result["ok"] is (change_ids == [])


def test_nonempty_approval_map_does_not_authorize_empty_ids():
    def backend(request):
        pytest.fail(f"Mismatched empty selection must not reach HTTP: {request.url}")

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[], previews={"1": "a" * 64})
    assert result["ok"] is False and result["error"]["code"] == "validation_error"


@pytest.mark.parametrize("previews", [None, {}, {"2": "a" * 64}, {"1": "A" * 64},
                                      {"1": "a" * 63}, {"1": 1}, {1: "a" * 64},
                                      {"1": "a" * 64, "2": "b" * 64}])
def test_invalid_approval_map_refuses_before_http(previews):
    def backend(request):
        pytest.fail(f"Invalid approval must not reach HTTP: {request.url}")

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=previews, force=True)
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"


@pytest.mark.parametrize("change_type", ["add_attachment", "set_field", "upsert_note", "future_write", ""])
def test_unreviewed_mutations_are_blocked(change_type):
    requests = []

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [{"id": 1, "change_type": change_type}]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews={"1": "a" * 64})
    assert result["ok"] is False
    assert result["error"]["code"] == "mcp_restricted"
    assert result["error"]["details"]["blocked"] == [{"id": 1, "change_type": change_type}]
    assert requests == ["GET"]


def test_missing_requested_rows_block_the_entire_write():
    requests = []

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [presented_row()]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1, 9999],
                      previews={"1": "a" * 64, "9999": "b" * 64})
    assert result["ok"] is False
    assert result["error"]["details"]["missing_change_ids"] == [9999]
    assert requests == ["GET"]


def test_selection_over_1000_is_rejected_before_any_http():
    ids = list(range(1, 1002))

    def backend(request):
        pytest.fail(f"Over-cap selection must not reach HTTP: {request.url}")

    result = run_tool(backend, pending.apply_pending_changes, change_ids=ids,
                      previews={str(i): "a" * 64 for i in ids})
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"


def test_1000_ids_use_one_request_with_exact_caller_authority():
    rows = [presented_row(i, "add_note", {"note_html": f"<p>Note {i}</p>"}) for i in range(1, 1001)]
    ids, previews, requests = [row["id"] for row in rows], approval_map(rows), []

    def backend(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.params["status"] == "all"
            return httpx.Response(200, json={"items": rows})
        return httpx.Response(200, json={"applied": 1000, "failed": 0})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=ids, previews=previews)
    assert result["ok"] is True and result["applied"] == 1000
    assert [request.method for request in requests] == ["GET", "POST"]
    assert json.loads(requests[-1].content) == {"change_ids": ids, "previews": previews, "force": False}


@pytest.mark.parametrize("outcome", ["force", "timeout", "conflict", "unreachable"])
def test_single_request_error_reports_unconfirmed_ids_without_retry(outcome):
    row = presented_row(1, "add_note", {"note_html": "<p>Presented A</p>"})
    previews, writes = approval_map([row]), []

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [row]})
        writes.append(json.loads(request.content))
        if outcome == "timeout":
            raise httpx.ReadTimeout("Response lost", request=request)
        if outcome == "unreachable":
            raise httpx.ConnectError("Disconnected", request=request)
        if outcome == "conflict":
            return httpx.Response(409, json={"error": "pending_write_conflict"})
        return httpx.Response(200, json={"requires_force": True, "error": "zotero_running"})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=previews)
    assert result["ok"] is False
    assert result["error"]["details"]["unconfirmed_change_ids"] == [1]
    assert result["error"]["retryable"] is False
    assert writes == [{"change_ids": [1], "previews": previews, "force": False}]


@pytest.mark.parametrize("field", ["approval_preview", "null", "version", "change_id", "item_key", "change_type",
                                  "payload", "edit_revision", "target", "generated_preview_sha256", "preview_sha256",
                                  "target.device", "target.inode", "target.database_path", "target.library_id",
                                  "target.library_type"])
def test_old_or_incomplete_backend_envelope_never_posts(field):
    row = presented_row()
    previews, requests = approval_map([row]), []
    if field == "approval_preview":
        row.pop(field)
    elif field == "null":
        row["approval_preview"] = None
    elif field.startswith("target."):
        row["approval_preview"]["target"].pop(field.split(".")[1])
    else:
        row["approval_preview"].pop(field)

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [row]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=previews)
    assert result["ok"] is False
    assert result["error"]["code"] == "backend_contract_error"
    assert result["error"]["retryable"] is False
    assert requests == ["GET"]


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("change_id", "1"), ("change_id", True), ("change_id", 2),
    ("item_key", "OTHER"), ("item_key", " "), ("change_type", "add_note"),
    ("payload", {"add_tags": ["different"]}), ("edit_revision", -1), ("edit_revision", True),
    ("target", None), ("target.inode", 0), ("target.device", True), ("target.library_id", -1),
    ("target.library_type", "group"), ("target.database_path", ""), ("target.extra", 1),
    ("generated_preview_sha256", "invalid"), ("preview_sha256", "A" * 64),
    ("preview_sha256", "a" * 64 + "\n"), ("extra", 1),
])
def test_malformed_backend_envelope_never_posts(field, value):
    row = presented_row()
    previews, requests = approval_map([row]), []
    owner = row["approval_preview"]
    parts = field.split(".")
    if len(parts) == 2:
        owner = owner[parts[0]]
    owner[parts[-1]] = value

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [row]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=previews, force=True)
    assert result["ok"] is False
    assert result["error"]["code"] == "backend_contract_error"
    assert result["error"]["retryable"] is False
    assert requests == ["GET"]


@pytest.mark.parametrize("payload", ["not-json", "[]", {"add_tags": [True]}])
def test_invalid_or_differently_typed_listed_payload_never_posts(payload):
    row = presented_row(payload={"add_tags": [1]})
    previews, requests = approval_map([row]), []
    row["payload_json"] = payload

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": [row]})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=previews)
    assert result["ok"] is False
    assert result["error"]["code"] == "backend_contract_error"
    assert requests == ["GET"]


@pytest.mark.parametrize("tool,kwargs,key", [
    (pending.list_pending_changes, {"status": "all"}, "items"),
    (search.get_paper, {"item_key": "PAPER1"}, "pending_changes"),
])
def test_read_tools_pass_full_approval_envelope_through(tool, kwargs, key):
    row = presented_row(1, "add_note", {"note_html": "<p>Full body é</p>", "note_title": "Exact title"})
    row["status"] = "failed"

    def backend(request):
        return httpx.Response(200, json={"items": [row] if request.url.path == "/api/pending" else []})

    result = run_tool(backend, tool, **kwargs)
    assert result["ok"] is True
    item, = result[key]
    assert item["payload"] == row["approval_preview"]["payload"]
    assert item["approval_preview"] == row["approval_preview"]
    assert item["status"] == "failed"


@pytest.mark.parametrize("changed", ["payload", "target", "revision", "aba", "no_op_save"])
@pytest.mark.parametrize("reverse", [False, True])
def test_stale_member_refuses_entire_selection_without_replacing_hash(changed, reverse):
    rows = [presented_row(1), presented_row(2, "add_note", {"note_html": "<p>Presented A</p>"})]
    previews = approval_map(rows)
    rows = deepcopy(rows)
    preview = rows[1]["approval_preview"]
    if changed == "payload":
        preview["payload"]["note_html"] = "<p>Durable B</p>"
        rows[1]["payload_json"] = json.dumps(preview["payload"])
    elif changed == "target":
        preview["target"]["inode"] += 1
    else:
        preview["edit_revision"] += 2 if changed == "aba" else 1
    preview.pop("preview_sha256")
    preview["preview_sha256"] = hashlib.sha256(json.dumps(
        preview, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    requests = []

    def backend(request):
        requests.append(request.method)
        return httpx.Response(200, json={"items": rows})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[2, 1] if reverse else [1, 2],
                      previews=previews, force=True)
    assert result["ok"] is False
    assert result["error"]["code"] == "pending_write_conflict"
    assert result["error"]["retryable"] is False
    assert requests == ["GET"]
    assert previews != approval_map(rows)


@pytest.mark.parametrize("change_type", sorted(pending.MCP_ALLOWED_CHANGE_TYPES))
@pytest.mark.parametrize("force", [False, True])
def test_allowed_subset_forwards_only_supplied_hashes_and_reports_blocked(change_type, force):
    row = presented_row(1, change_type, {"text": "Full <escaped> payload"})
    previews, writes = {**approval_map([row]), "2": "b" * 64}, []

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [row, {"id": 2, "change_type": "upsert_note"}]})
        writes.append(json.loads(request.content))
        return httpx.Response(200, json={"applied": 1, "failed": 0, "backup_path": "owned.sqlite"})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1, 2, 1], previews=previews, force=force)
    assert result["ok"] is True
    assert result["requested_change_ids"] == [1]
    assert result["mcp_blocked_change_ids"] == [{"id": 2, "change_type": "upsert_note"}]
    assert writes == [{"change_ids": [1], "previews": {"1": previews["1"]}, "force": force}]
    assert result["backup_paths"] == ["owned.sqlite"]


def test_mcp_apply_matches_real_api_and_sqlite_failure(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from unittest.mock import Mock

    from tests._generated_write_fixtures import install_generated_writer
    from tests._zotero_fixtures import add_library_item
    from zotero_summarizer.api.routes.pending import router
    from zotero_summarizer.storage import repositories as db

    native, writer = install_generated_writer(tmp_path, monkeypatch)
    add_library_item(native, item_key="PAPER1", title="Paper")
    db.insert_pending_changes("PAPER1", "Paper", [{"change_type": "add_note", "payload": {"note_html": "note"}}])
    dispatch = Mock(side_effect=ValueError("disk full"))
    monkeypatch.setattr(writer, "_dispatch_change", dispatch)
    app = FastAPI()
    app.include_router(router)
    client = httpx.AsyncClient

    async def apply_presented():
        async with client(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
            response = await api.get("/api/pending", params={"status": "pending"})
            assert response.status_code == 200
            row, = response.json()["items"]
        return row, await pending.apply_pending_changes(change_ids=[row["id"]], previews=approval_map([row]))

    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.ASGITransport(app=app), **kw,
    )):
        row, result = asyncio.run(apply_presented())

    assert result["ok"] is False
    details = result["error"]["details"]
    assert details["applied"] == 0 and details["failed"] == 1
    assert details["failed_items"] == [{"id": row["id"], "error": "disk full", "expected_io": False}]
    dispatch.assert_called_once()
    saved, = db.get_pending_changes_by_ids([row["id"]])
    assert saved["status"] == "failed" and saved["error_message"] == "disk full"
    assert saved["backup_path"] == details["backup_path"]
    assert details["backup_paths"] == [saved["backup_path"]]
    for path in (native, saved["backup_path"]):
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone()[0] == 0


def test_current_apply_receipt_has_no_inbox_only_fields():
    row = presented_row()

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [row]})
        return httpx.Response(200, json={"applied": 1, "failed": 0, "backup_path": "owned.sqlite"})

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=approval_map([row]))
    assert result == {"ok": True, "requested_change_ids": [1], "applied": 1, "failed": 0,
                      "backup_path": "owned.sqlite", "backup_paths": ["owned.sqlite"],
                      "failed_items": [], "batches": 1}


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
            asyncio.run(mcp.call_tool("apply_pending_changes", {"change_ids": [value], "previews": {"1": "a" * 64}}))


@pytest.mark.parametrize("arguments", [{}, {"change_ids": [1]}, {"previews": {"1": "a" * 64}}])
def test_registered_mcp_tool_requires_both_explicit_arguments(arguments):
    from mcp.server.fastmcp.exceptions import ToolError
    from zotero_summarizer.mcp.server import mcp

    with patch.object(pending, "_api_request", side_effect=AssertionError("No HTTP for missing authority")):
        with pytest.raises(ToolError, match="validation error"):
            asyncio.run(mcp.call_tool("apply_pending_changes", arguments))


@pytest.mark.parametrize("data", [{}, {"applied": 1, "failed": -1}, {"applied": True, "failed": 0}])
def test_invalid_write_receipt_is_not_success(data):
    row = presented_row(1, "add_note", {"note_html": "note"})

    def backend(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [row]})
        return httpx.Response(200, json=data)

    result = run_tool(backend, pending.apply_pending_changes, change_ids=[1], previews=approval_map([row]))
    assert result["ok"] is False
    assert result["error"]["code"] == "backend_contract_error"
    assert result["error"]["details"]["unconfirmed_change_ids"] == [1]
    assert result["error"]["retryable"] is False
