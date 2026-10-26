"""Explicit invocation inventory; transport entry is not remote acceptance or kill proof."""
from __future__ import annotations

from contextlib import contextmanager
import json
from threading import RLock
from uuid import uuid4


def _generation_endpoint(base_url, operation):
    import httpx

    base = httpx.URL(base_url)
    path = base.raw_path if base.raw_path.endswith(b"/") else base.raw_path + b"/"
    return str(base.copy_with(raw_path=path + operation.lstrip("/").encode("ascii")))


class _RequestStartRefused(RuntimeError):
    """A predecessor was unresolved at the serialized pre-send persistence boundary."""


def _summarize_requests(rows):
    started = sum(row["started"] for row in rows)
    terminal = sum(row["terminal"] for row in rows)
    invalid = any(row["invalid_events"] for row in rows)
    coherent = bool(rows) and len(rows) == started == terminal and not invalid
    hold = any(row["started"] and (not row["terminal"] or row["invalid_events"]) for row in rows)
    uncertain = any(not row["started"] and not row["not_submitted"] for row in rows)
    return {"expected": len(rows), "started": started, "terminal": terminal,
            "invalid_events": invalid, "coherent": coherent,
            "successful": coherent and all(row["successful"] for row in rows),
            "ownership": "unresolved" if hold else "unknown" if uncertain else
                         "completed" if started else "not_submitted",
            "hold": hold, "uncertainty": uncertain, "fence": hold or uncertain}


class RequestInventory:
    """Owned by one attempt. No ambient observer, credentials, bodies or exceptions."""

    def __init__(self, run_id: str, *, changed=None, before_start=None):
        self.run_id = run_id
        self._lock = RLock()
        self._requests = []
        self._native_ids = set()
        self._changed = changed
        self._before_start = before_start

    def expect(self, identity: str, model: str, endpoint: str, *, local: bool):
        request = _RequestObservation(self, identity, model, endpoint, local=local)
        with self._lock:
            self._requests.append(request)
            self._notify(request)
        return request

    def _notify(self, request):
        if self._changed is not None:
            self._changed(request.receipt())

    def receipts(self):
        with self._lock:
            return [request.receipt() for request in self._requests]

    def summary(self, *, start=0):
        return _summarize_requests(self.receipts()[start:])


class _RequestObservation:
    def __init__(self, inventory, identity: str, model: str, endpoint: str, *, local: bool):
        self._inventory = inventory
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._transport = None
        self._native_id = None
        self._row = {"run_id": inventory.run_id, "request_id": uuid4().hex,
                     "identity": identity, "local": local, "expected": True,
                     "started": False, "terminal": False, "successful": False, "not_submitted": False,
                     "invalid_events": False, "reason_code": "completion_unobserved"}

    def receipt(self):
        return dict(self._row)

    def _invalid(self):
        self._row.update(invalid_events=True, successful=False, reason_code="request_evidence_invalid")
        self._inventory._notify(self)
        raise ValueError("Request ownership evidence was missing, foreign, duplicate or out of order")

    def refuse_before_dispatch(self):
        """Positive inline refusal at the native boundary, never inferred from missing telemetry."""
        with self._inventory._lock:
            if self._transport is not None:
                self._invalid()
            self._row["not_submitted"] = True
            self._inventory._notify(self)

    def native_started(self, run_id):
        with self._inventory._lock:
            if run_id is None or self._native_id is not None or self._row["terminal"] or run_id in self._inventory._native_ids:
                # This inline native callback raises before this invocation's send.
                if self._transport is None:
                    self.refuse_before_dispatch()
                self._invalid()
            self._native_id = run_id
            self._inventory._native_ids.add(run_id)

    def transport_started(self, request):
        """Called by the owned HTTPX client, immediately before its transport send."""
        if self._inventory._before_start is not None:
            try:
                self._inventory._before_start()
            except BaseException:
                with self._inventory._lock:
                    if self._transport is None:
                        self.refuse_before_dispatch()
                raise
        with self._inventory._lock:
            if self._transport is not None or self._row["terminal"]:
                self._invalid()
            try:
                actual_model = json.loads(request.content).get("model")
                matches = (request.method == "POST" and str(request.url) == self._endpoint
                           and actual_model == self._model)
            except (ValueError, TypeError, AttributeError):
                matches = False
            if not matches:
                self.refuse_before_dispatch()
                self._invalid()
            # Persist transport entry before send; this does NOT prove remote acceptance.
            self._transport = request
            self._row["started"] = True
            try:
                self._inventory._notify(self)
            except _RequestStartRefused:
                self._row.update(started=False, not_submitted=True, reason_code="local_request_fence")
                self._transport = None
                self._inventory._notify(self)
                raise

    def terminal(self, model, reason, *, native_id=None, native_required=False):
        """Only a decoded native response for this exact invocation can reconcile it."""
        with self._inventory._lock:
            if (not self._row["started"] or self._row["terminal"] or self._row["invalid_events"]
                    or model != self._model or not isinstance(reason, str)
                    or reason not in {"stop", "length", "end_turn", "stop_sequence", "max_tokens"}
                    or (native_required and (native_id is None or native_id != self._native_id))):
                self._invalid()
            self._row.update(terminal=True, successful=reason in {"stop", "end_turn", "stop_sequence"},
                             reason_code="request_output_truncated" if reason in {"length", "max_tokens"} else "")
            self._inventory._notify(self)


def verification_http_client(request_hook, *, transport=None):
    """Use the existing HTTPX boundary, without retries, redirects or ambient proxy lookup."""
    import httpx

    return httpx.Client(follow_redirects=False, trust_env=False, transport=transport,
                        event_hooks={"request": [request_hook]})


@contextmanager
def owned_request(guard):
    """Never allow a second invocation to borrow an earlier completion or cache hit."""
    guard.begin_request()
    try:
        yield
        guard.finish_request()
    finally:
        guard.end_request()
