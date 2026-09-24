"""Global ordering/pagination at the public MCP/HTTP boundary."""
import asyncio
from unittest.mock import patch

import httpx
import pytest

from zotero_summarizer.mcp.tools.search import search_papers


@pytest.mark.parametrize("sort_by", ["score", "priority", "title", "recency"])
def test_search_sorts_complete_filtered_set_before_pagination(sort_by):
    scores = [2, 1.5, 1, .5, 4, 3.5, 3, 2.5]
    items = [{"item_key": str(i), "title": f"Paper {8-i}", "date_modified": str(i)} for i in range(8)]
    source_requests = []

    def backend(request):
        if request.url.path == "/api/zotero/items":
            params = request.url.params
            assert params["search"] == "paper"
            assert params["collection"] == "COLLECTION"
            assert params["tag"] == "read"
            offset = int(params["offset"])
            source_requests.append(offset)
            # Backend pages may be shorter than the requested limit.
            return httpx.Response(200, json={"items": items[offset:offset+3], "total": 8})
        index = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"composite_score": scores[index], "reading_priority": "must_read"})

    async def pages():
        cursor = None
        seen = []
        for _ in range(8):
            result = await search_papers(query="paper", collection="COLLECTION", tag="read",
                                         sort_by=sort_by, limit=1, cursor=cursor, score_min=1)
            assert result["ok"] is True
            assert result["source_total"] == 8
            assert result["filtered_count"] == 7
            seen.extend(row["item_key"] for row in result["items"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        return seen

    client = httpx.AsyncClient
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.MockTransport(backend), **kw,
    )):
        seen = asyncio.run(pages())

    expected = ["4", "5", "6", "7", "0", "1", "2"] if sort_by in {"score", "priority"} else ["7", "6", "5", "4", "2", "1", "0"]
    assert seen == expected
    assert len(seen) == len(set(seen))
    assert 6 in source_requests


@pytest.mark.parametrize("failure", ["stalled", "duplicate", "changed_total", "too_large", "triage_error", "unsafe_key"])
def test_incomplete_search_never_claims_a_sorted_complete_result(failure):
    calls = []

    def backend(request):
        calls.append(request.url.path)
        if request.url.path == "/api/zotero/items":
            offset = int(request.url.params["offset"])
            total = 10001 if failure == "too_large" else 2
            if offset and failure == "stalled":
                return httpx.Response(200, json={"items": [], "total": total})
            if offset and failure == "changed_total":
                total = 3
            key = "A/../B" if failure == "unsafe_key" else "A" if failure == "duplicate" else str(offset)
            return httpx.Response(200, json={"items": [{"item_key": key}], "total": total})
        if failure == "triage_error":
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.AsyncClient
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.MockTransport(backend), **kw,
    )):
        result = asyncio.run(search_papers(sort_by="score"))
    assert result["ok"] is False
    assert "filtered_count" not in result
    assert len(calls) <= 4
    assert "/api/B" not in calls


@pytest.mark.parametrize("cursor", ["5:1", "7", "g:-1", "garbage"])
def test_old_or_invalid_search_cursor_requires_restart(cursor):
    def backend(request):
        pytest.fail(f"Invalid cursor must not send HTTP: {request.url}")

    client = httpx.AsyncClient
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client(
        transport=httpx.MockTransport(backend), **kw,
    )):
        result = asyncio.run(search_papers(cursor=cursor))
    assert result["error"]["code"] == "validation_error"
