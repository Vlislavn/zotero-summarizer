#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
TRIAGE_TIMEOUT_SECONDS = int(os.getenv("SMOKE_TRIAGE_TIMEOUT_SECONDS", "420"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("SMOKE_REQUEST_TIMEOUT_SECONDS", "45"))


class SmokeFailure(RuntimeError):
    pass


@dataclass
class CheckResult:
    name: str
    ok: bool
    elapsed_ms: float
    message: str


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    url = BASE_URL + path
    headers: dict[str, str] = {}
    body: bytes | None = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = Request(url=url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            return int(resp.status), (json.loads(raw) if raw.strip() else {})
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            data = {"raw": raw}
        return int(exc.code), data
    except URLError as exc:
        raise SmokeFailure(f"Network error for {method} {path}: {exc}") from exc


def _run_check(name: str, fn: Callable[[], str]) -> CheckResult:
    started = time.perf_counter()
    try:
        message = fn()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return CheckResult(name=name, ok=True, elapsed_ms=elapsed_ms, message=message)
    except Exception as exc:  # pragma: no cover - smoke script runtime reporting
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return CheckResult(name=name, ok=False, elapsed_ms=elapsed_ms, message=str(exc))


def _expect_status(status: int, expected: int, context: str) -> None:
    if status != expected:
        raise SmokeFailure(f"{context}: expected HTTP {expected}, got {status}")


def _build_query(path: str, params: dict[str, Any]) -> str:
    encoded = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{path}?{encoded}" if encoded else path


def main() -> int:
    print(f"Smoke target: {BASE_URL}")

    state: dict[str, Any] = {}
    checks: list[CheckResult] = []

    def check_health() -> str:
        status, data = _request("GET", "/api/health")
        _expect_status(status, 200, "GET /api/health")
        if data.get("status") != "ok":
            raise SmokeFailure(f"Health returned non-ok status: {data.get('status')}")
        return "server healthy"

    def check_zotero_status() -> str:
        status, data = _request("GET", "/api/zotero/status")
        _expect_status(status, 200, "GET /api/zotero/status")
        if not data.get("available"):
            raise SmokeFailure(f"Zotero unavailable: {data.get('error')}")
        stats = data.get("stats") or {}
        return f"items={stats.get('total_items', 0)} collections={stats.get('total_collections', 0)}"

    def check_collections() -> str:
        status, data = _request("GET", "/api/zotero/collections")
        _expect_status(status, 200, "GET /api/zotero/collections")
        collections = data.get("items") or []
        if collections:
            state["collection_key"] = collections[0].get("key")
        return f"root_collections={len(collections)}"

    def check_tags() -> str:
        path = _build_query("/api/zotero/tags", {"limit": 50})
        status, data = _request("GET", path)
        _expect_status(status, 200, "GET /api/zotero/tags")
        tags = data.get("items") or []
        if tags:
            state["tag_name"] = tags[0].get("tag")
        return f"tags={len(tags)}"

    def check_items_list() -> str:
        first_page_path = _build_query("/api/zotero/items", {"limit": 50, "offset": 0})
        status, data = _request("GET", first_page_path)
        _expect_status(status, 200, "GET /api/zotero/items")
        items = data.get("items") or []
        total = int(data.get("total") or 0)
        if total <= 0 or not items:
            raise SmokeFailure("No items returned from /api/zotero/items")

        chosen: dict[str, Any] | None = None
        with_pdf = [item for item in items if item.get("has_pdf")]
        if with_pdf:
            chosen = with_pdf[0]

        # Some first pages may contain no local PDFs; scan further pages deterministically.
        scan_limit = min(total, 2000)
        scan_offset = 50
        while chosen is None and scan_offset < scan_limit:
            page_path = _build_query("/api/zotero/items", {"limit": 100, "offset": scan_offset})
            page_status, page_data = _request("GET", page_path)
            _expect_status(page_status, 200, "GET /api/zotero/items paginated scan")
            page_items = page_data.get("items") or []
            if not page_items:
                break
            page_with_pdf = [item for item in page_items if item.get("has_pdf")]
            if page_with_pdf:
                chosen = page_with_pdf[0]
                break
            scan_offset += len(page_items)

        if chosen is None:
            chosen = items[0]

        state["item_key"] = chosen.get("item_key")
        state["item_title"] = chosen.get("title")
        state["item_priority_before"] = chosen.get("reading_priority")

        title_words = str(chosen.get("title") or "").split()
        if title_words:
            state["search_query"] = title_words[0][:20]
        return f"total={total} page_items={len(items)} chosen={state['item_key']}"

    def check_item_filters() -> str:
        collection_key = state.get("collection_key")
        tag_name = state.get("tag_name")
        search_query = state.get("search_query")

        if collection_key:
            path = _build_query("/api/zotero/items", {"collection": collection_key, "limit": 10, "offset": 0})
            status, _ = _request("GET", path)
            _expect_status(status, 200, "GET /api/zotero/items?collection")

        if tag_name:
            path = _build_query("/api/zotero/items", {"tag": tag_name, "limit": 10, "offset": 0})
            status, _ = _request("GET", path)
            _expect_status(status, 200, "GET /api/zotero/items?tag")

        if search_query:
            path = _build_query("/api/zotero/items", {"search": search_query, "limit": 10, "offset": 0})
            status, _ = _request("GET", path)
            _expect_status(status, 200, "GET /api/zotero/items?search")

        return "collection/tag/search filters ok"

    def check_item_detail() -> str:
        item_key = state.get("item_key")
        if not item_key:
            raise SmokeFailure("No item key available for detail endpoint")
        status, data = _request("GET", "/api/zotero/items/" + quote(item_key))
        _expect_status(status, 200, "GET /api/zotero/items/{item_key}")
        state["item_has_pdf"] = bool(data.get("has_pdf"))
        state["item_priority_before"] = data.get("reading_priority")
        return f"detail loaded has_pdf={state['item_has_pdf']}"

    def check_pending_before() -> str:
        status, data = _request("GET", "/api/pending?status=pending&limit=1000")
        _expect_status(status, 200, "GET /api/pending")
        pending = data.get("items") or []
        state["pending_before_ids"] = {int(item["id"]) for item in pending if int(item.get("id") or 0) > 0}
        return f"pending_before={len(state['pending_before_ids'])}"

    def check_run_triage_job() -> str:
        item_key = state.get("item_key")
        if not item_key:
            raise SmokeFailure("No item key available to run triage")
        if not state.get("item_has_pdf"):
            raise SmokeFailure("Chosen item has no local PDF; triage smoke requires a PDF-backed item")

        status, data = _request("POST", "/api/triage/run", {"item_keys": [item_key], "queue_changes": True})
        _expect_status(status, 200, "POST /api/triage/run")
        job_id = str(data.get("job_id") or "")
        if not job_id:
            raise SmokeFailure("Triage response did not include job_id")

        deadline = time.time() + TRIAGE_TIMEOUT_SECONDS
        final_job: dict[str, Any] | None = None
        while time.time() < deadline:
            status, job = _request("GET", "/api/triage/jobs/" + quote(job_id))
            _expect_status(status, 200, "GET /api/triage/jobs/{job_id}")
            if job.get("status") in {"completed", "failed"}:
                final_job = job
                break
            time.sleep(2)

        if not final_job:
            raise SmokeFailure(f"Triage job {job_id} did not finish within {TRIAGE_TIMEOUT_SECONDS} seconds")
        if final_job.get("status") != "completed":
            errors = final_job.get("errors") or []
            first_error = errors[0].get("error") if errors else "unknown"
            raise SmokeFailure(f"Triage job failed: {first_error}")

        item_errors = final_job.get("errors") or []
        if item_errors:
            first_error = str(item_errors[0].get("error") or "unknown")
            raise SmokeFailure(f"Triage job completed with item errors: {first_error}")

        state["triage_job_id"] = job_id
        return f"job={job_id} completed={final_job.get('completed')}/{final_job.get('total')}"

    def check_triage_jobs_list() -> str:
        status, data = _request("GET", "/api/triage/jobs")
        _expect_status(status, 200, "GET /api/triage/jobs")
        items = data.get("items") or []
        return f"jobs_returned={len(items)}"

    def check_pending_count() -> str:
        status, data = _request("GET", "/api/pending/count")
        _expect_status(status, 200, "GET /api/pending/count")
        return f"pending_count={int(data.get('count') or 0)}"

    def check_priority_override_queue() -> str:
        item_key = state.get("item_key")
        item_title = state.get("item_title") or item_key
        if not item_key:
            raise SmokeFailure("No item key available for priority override")

        current = str(state.get("item_priority_before") or "").strip()
        ordered = ["must_read", "should_read", "could_read", "dont_read"]
        new_priority = next((value for value in ordered if value != current), "must_read")

        status, data = _request(
            "POST",
            "/api/pending/override-priority",
            {
                "item_key": item_key,
                "item_title": item_title,
                "new_priority": new_priority,
            },
        )
        _expect_status(status, 200, "POST /api/pending/override-priority")

        state["override_queued"] = int(data.get("queued") or 0)

        status_after, data_after = _request("GET", "/api/pending?status=pending&limit=1000")
        _expect_status(status_after, 200, "GET /api/pending after override")
        pending_after = data_after.get("items") or []
        after_ids = {int(item["id"]) for item in pending_after if int(item.get("id") or 0) > 0}

        before_ids = state.get("pending_before_ids") or set()
        created_ids = sorted(after_ids - before_ids)
        state["created_pending_ids"] = created_ids

        if state["override_queued"] > 0 and not created_ids:
            raise SmokeFailure("Override queued changes but no new pending IDs were detected")

        return f"override_queued={state['override_queued']} created_pending_ids={len(created_ids)}"

    def check_reject_created_pending() -> str:
        created_ids = state.get("created_pending_ids") or []
        if not created_ids:
            return "no newly created pending items to reject"

        status, data = _request("POST", "/api/pending/reject", {"change_ids": created_ids})
        _expect_status(status, 200, "POST /api/pending/reject")
        updated = int(data.get("updated") or 0)
        if updated <= 0:
            raise SmokeFailure("Reject endpoint returned zero updated rows for created pending IDs")
        return f"rejected={updated}"

    def check_apply_endpoint_noop() -> str:
        status, data = _request("POST", "/api/pending/apply", {"change_ids": [2147483647], "force": False})
        _expect_status(status, 200, "POST /api/pending/apply noop")
        return f"applied={data.get('applied', 0)} failed={data.get('failed', 0)}"

    def check_results_endpoints() -> str:
        status, data = _request("GET", "/api/results?scope=latest&limit=10")
        _expect_status(status, 200, "GET /api/results")
        total = int(data.get("total") or 0)

        item_key = state.get("item_key")
        if not item_key:
            raise SmokeFailure("No item key available for /api/results/{item_id}")

        detail_status, _ = _request("GET", "/api/results/" + quote(item_key))
        _expect_status(detail_status, 200, "GET /api/results/{item_id}")
        return f"results_total={total}"

    def check_config_roundtrip() -> str:
        status, data = _request("GET", "/api/config")
        _expect_status(status, 200, "GET /api/config")

        put_status, put_data = _request("PUT", "/api/config", data)
        _expect_status(put_status, 200, "PUT /api/config")
        if put_data.get("status") != "ok":
            raise SmokeFailure("Config PUT did not return status=ok")
        return "config roundtrip ok"

    check_fns: list[tuple[str, Callable[[], str]]] = [
        ("health", check_health),
        ("zotero_status", check_zotero_status),
        ("collections", check_collections),
        ("tags", check_tags),
        ("items", check_items_list),
        ("item_filters", check_item_filters),
        ("item_detail", check_item_detail),
        ("pending_before", check_pending_before),
        ("triage_run", check_run_triage_job),
        ("triage_jobs", check_triage_jobs_list),
        ("pending_count", check_pending_count),
        ("priority_override_queue", check_priority_override_queue),
        ("reject_created_pending", check_reject_created_pending),
        ("pending_apply_noop", check_apply_endpoint_noop),
        ("results", check_results_endpoints),
        ("config_roundtrip", check_config_roundtrip),
    ]

    for name, fn in check_fns:
        result = _run_check(name, fn)
        checks.append(result)
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {name} ({result.elapsed_ms:.0f} ms) - {result.message}")

    passed = sum(1 for item in checks if item.ok)
    failed = len(checks) - passed

    print("\nSummary")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    if failed:
        print("\nFailures:")
        for item in checks:
            if not item.ok:
                print(f"- {item.name}: {item.message}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
