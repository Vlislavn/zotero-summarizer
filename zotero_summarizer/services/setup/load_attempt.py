"""Same-process setup callback ownership; waiter cancellation never unlocks work."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Callable
from uuid import UUID

from zotero_summarizer.services.setup.load_admission import assess_setup_load


@dataclass
class _LoadBoundary:
    state: Lock = field(default_factory=Lock)
    worker: Lock = field(default_factory=Lock)
    hold: str | None = None


_BOUNDARY = _LoadBoundary()


class SetupLoadAbandoned(RuntimeError):
    """The waiter withdrew before the next submission."""


class SetupAttempt:
    def __init__(self):
        self.boundary = _BOUNDARY
        self.aborted: str | None = None
        self.accepted = False
        self.attempted = False
        self.callback_state = "not_started"
        self.local_submitted = False
        self.admission: dict = {}
        self._expected_requests = 0
        self._request_reasons: dict[UUID, str | None] = {}
        self._seen_request_ids: set[UUID] = set()
        self._invalid_request_events = False

    def abandon(self, reason: str) -> dict:
        with self.boundary.state:
            self.aborted = reason
            if self.local_submitted:
                self.boundary.hold = self.boundary.hold or f"waiter_{reason}"
            return self._receipt()

    def _requests_completed(self) -> bool:
        return (self.attempted and not self.aborted and not self._invalid_request_events
                and self._expected_requests > 0
                and self._expected_requests == len(self._request_reasons)
                and all(reason in {"stop", "length"} for reason in self._request_reasons.values()))

    def _receipt(self) -> dict:
        ownership = "unresolved" if self.attempted else "not_submitted"
        if self._requests_completed() and not self.boundary.hold:
            ownership = "completed"
        receipt = {"accepted": self.accepted, "attempted": self.attempted,
                   "callback_state": self.callback_state, "ownership": ownership,
                   "admission": self.admission}
        if self._expected_requests or self._request_reasons or self._invalid_request_events:
            receipt["request_completion"] = {
                "expected": self._expected_requests, "started": len(self._request_reasons),
                "terminal": sum(reason is not None for reason in self._request_reasons.values()),
                "invalid_events": self._invalid_request_events,
            }
        return receipt

    def receipt(self) -> dict:
        with self.boundary.state:
            return self._receipt()

    def is_abandoned(self) -> bool:
        with self.boundary.state:
            return self.aborted is not None

    def _submit(self) -> None:
        if self.aborted:
            raise SetupLoadAbandoned(f"setup attempt abandoned: {self.aborted}")
        self.attempted = True
        self.local_submitted = self.admission.get("status") == "admitted"

    def submit(self) -> None:
        with self.boundary.state:
            self._submit()

    def request_expected(self) -> None:
        with self.boundary.state:
            if self.aborted:
                raise SetupLoadAbandoned(f"setup attempt abandoned: {self.aborted}")
            if self.callback_state != "active" or self._invalid_request_events:
                raise ValueError("Setup request has no active callback ownership")
            self._expected_requests += 1

    def request_started(self, run_id: UUID) -> None:
        with self.boundary.state:
            if self.aborted:
                raise SetupLoadAbandoned(f"setup attempt abandoned: {self.aborted}")
            if (self.callback_state != "active" or self._invalid_request_events
                    or not isinstance(run_id, UUID) or run_id in self._seen_request_ids
                    or len(self._request_reasons) >= self._expected_requests):
                self._invalid_request_events = True
                raise ValueError("Setup request start is duplicate, unexpected or unowned")
            self._submit()
            self._seen_request_ids.add(run_id)
            self._request_reasons[run_id] = None

    def request_finished(self, run_id: UUID, terminal_reason: str | None) -> None:
        with self.boundary.state:
            if (self.callback_state != "active" or not isinstance(run_id, UUID)
                    or run_id not in self._request_reasons
                    or self._request_reasons[run_id] is not None
                    or terminal_reason not in ("stop", "length")):
                self._invalid_request_events = True
                raise ValueError("Setup request completion is duplicate, unknown or unowned")
            self._request_reasons[run_id] = terminal_reason


def local_load_hold() -> str | None:
    with _BOUNDARY.state:
        return _BOUNDARY.hold


def refused_load(reason: str, *, admission=None, detail="") -> dict:
    return {"status": "fail", "detail": detail or f"Local verification deferred: {reason}. "
            "Review endpoint settings; configuration and separately confirmed downloads remain available.",
            "reason_code": reason, "attempted": False, "accepted": False,
            "callback_state": "not_started", "ownership": "not_submitted",
            "admission": admission or {}}


def abandoned_load(attempt: SetupAttempt, reason: str, resolved=None) -> dict:
    from zotero_summarizer.services.setup.load_admission import load_identity

    receipt = attempt.abandon(reason)
    if resolved is not None and receipt["admission"].get("identity") != load_identity(resolved):
        receipt = {"accepted": False, "attempted": False, "callback_state": "not_started",
                   "ownership": "not_submitted", "admission": {}}
    return {**refused_load(f"waiter_{reason}"), **receipt, "abort_remaining": True,
            "detail": f"Setup waiter {reason}; callback/remote termination is not established."}


def _accept(attempt: SetupAttempt, admission: dict) -> bool:
    with attempt.boundary.state:
        if attempt.aborted:
            return False
        attempt.accepted = True
        attempt.attempted = False
        attempt.local_submitted = False
        attempt.callback_state = "active"
        attempt.admission = admission
        attempt._expected_requests = 0
        attempt._request_reasons = {}
        attempt._invalid_request_events = False
        return True


def _invoke_load(attempt: SetupAttempt, load: Callable) -> dict:
    try:
        result = load(attempt.submit)
    except Exception as exc:  # noqa: BLE001 — setup diagnostic boundary
        result = {"status": "fail", "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        with attempt.boundary.state:
            attempt.callback_state = "returned"
            if attempt.local_submitted and not attempt._requests_completed():
                attempt.boundary.hold = attempt.boundary.hold or "operation_finality_unproven"
    with attempt.boundary.state:
        completed = attempt._requests_completed()
        local = attempt.admission.get("status") == "admitted"
        receipt = attempt._receipt()
        if local and attempt.boundary.hold:
            return {**result, **receipt, "status": "fail", "reason_code": "local_load_hold",
                    "detail": f"{result.get('detail', '')} Local response/callback returned; "
                    "operation finality (including earlier transport retries) is unproven. HOLD remains."}
        if local and not completed:
            return {**result, **receipt, "status": "fail", "reason_code": "request_completion_unproven",
                    "detail": result.get("detail") or "No complete observed setup request was established."}
        if local and "length" in attempt._request_reasons.values():
            return {**result, **receipt, "status": "fail", "reason_code": "request_output_truncated",
                    "detail": result.get("detail") or "Observed terminal length; response output is truncated."}
        return {**result, **receipt}


def run_setup_load(settings, resolved, load: Callable, *, attempt=None, policy=None, facts=None) -> dict:
    """The actual synchronous callback acquires/releases its own nonblocking lock."""
    attempt = attempt or SetupAttempt()
    boundary = attempt.boundary
    if not resolved.provider.is_local:
        admission = assess_setup_load(settings, resolved)
        if not _accept(attempt, admission):
            return refused_load("attempt_abandoned")
        return _invoke_load(attempt, load)
    with boundary.state:
        if attempt.aborted:
            return refused_load("attempt_abandoned")
        if not boundary.worker.acquire(blocking=False):
            return refused_load("setup_load_busy")
    try:
        with boundary.state:
            if boundary.hold:
                return refused_load("local_load_hold", detail=f"Local verification on HOLD: {boundary.hold}. "
                                    "Review endpoint settings; retry/restart is not proof of completion.")
        admission = assess_setup_load(settings, resolved, policy=policy, facts=facts)
        if admission["status"] != "admitted":
            return refused_load(admission["reason_code"], admission=admission,
                                detail=admission.get("detail", ""))
        if not _accept(attempt, admission):
            return refused_load("attempt_abandoned", admission=admission)
        return _invoke_load(attempt, load)
    finally:
        # No waiter or cancellation handler can reach this release.
        boundary.worker.release()


def load_check_row(check_id: str, results: list[dict], success: str) -> dict:
    from zotero_summarizer.services.setup.doctor_environment import _row

    grouped = {}
    for result in results:
        key = (result["provider"], result["model"], result.get("detail") or result["status"])
        grouped.setdefault(key, []).append(result["stage"])
    detail = "; ".join(f"{p}/{m} ({', '.join(stages)}): {outcome}"
                       for (p, m, outcome), stages in grouped.items())
    failed = any(result["status"] != "operational" for result in results)
    guarded = any(result.get("reason_code") for result in results)
    row = _row(check_id, "needs_action" if failed else "ready",
               "LLM verification needs action" if guarded else (
                   f"{check_id.replace('_', ' ').title()} failed" if failed else success), detail,
               action="Review endpoint settings" if guarded else "Retry inference",
               recovery_action="open_ai_settings" if guarded else "retry")
    row["load_receipts"] = results
    return row


def redact_load_receipts(value, secrets: list[str]):
    if isinstance(value, dict):
        return {key: redact_load_receipts(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_load_receipts(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
    return value


def withhold_local_ready(settings, rows: list[dict]) -> None:
    if not local_load_hold():
        return
    from zotero_summarizer.models.providers import STAGES, resolve_stage
    from zotero_summarizer.services._common import read_config

    from yaml import YAMLError

    message = "Local verification remains on HOLD"
    detail = "Operation finality is unproven; retry/restart is not proof of completion."
    try:
        config = read_config(settings.config_path, settings.calibration_path)
        if not config.llm_enabled or not any(
            resolve_stage(config.llm_routing, s).provider.is_local for s in STAGES
        ):
            return
    except (OSError, ValueError, YAMLError) as exc:
        message = "Current AI configuration could not be validated"
        detail = f"{type(exc).__name__}: current configuration read/validation failed."
    for row in rows:
        if row["id"] in {"llm_inference", "dry_run"} and row["status"] == "ready":
            row.update(status="needs_action", message=message, detail=detail,
                       recovery={"label": "Review endpoint settings", "action": "open_ai_settings"})


async def check_setup_stages(settings, routing, *, stages=None, attempt=None) -> dict:
    from zotero_summarizer.models.providers import STAGES
    from zotero_summarizer.services.llm import operational_check

    attempt = attempt or SetupAttempt()

    def probe(resolved):
        return run_setup_load(settings, resolved, lambda submit: operational_check.probe_provider(
            resolved.provider, resolved.model, before_prompt=submit,
            request_observer=attempt if resolved.provider.is_local else None), attempt=attempt)

    return await operational_check.check_routing_stages(
        routing, stages=STAGES if stages is None else stages, probe=probe,
        on_abandon=lambda resolved, reason: abandoned_load(attempt, reason, resolved))
