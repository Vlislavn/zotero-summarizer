"""Bound the setup waiter; only its synchronous callback releases exclusion."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

_WORKER = Lock()
_CURRENT = ContextVar("setup_attempt", default=None)


def current_attempt():
    return _CURRENT.get()


class SetupAttempt:
    def __init__(self):
        self.run_id = uuid4().hex
        self._state = Lock()
        self._aborted = Event()
        self.reason = ""
        self.callback_state = "not_started"
        self.callback_started = False
        self.finished = False
        from zotero_summarizer.integrations.llm_requests import RequestInventory

        self._settings = None
        self.requests = RequestInventory(self.run_id, changed=self._requests_changed,
                                         before_start=self.require_active)

    @contextmanager
    def scope(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    def _requests_changed(self, _request):
        if self._settings is not None:
            from zotero_summarizer.services.setup.doctor_requests import record_requests

            record_requests(self._settings, self.run_id, self.requests.receipts(), self.requests.summary())

    def bind_client(self, configuration, provider, model):
        """Freeze expected identity before construction; actual evidence comes from the client."""
        from zotero_summarizer.services.setup.doctor_requests import local_request_fence

        self.require_active()
        if self._settings is not None and self._settings.data_dir != configuration.data_dir:
            raise ValueError("A Setup attempt cannot change its selected data directory")
        self._settings = configuration
        if provider.is_local and local_request_fence(configuration):
            raise RuntimeError("Unresolved local request ownership prevents a successor request")
        identity = hashlib.sha256(json.dumps({"provider": provider.model_dump(mode="json"), "model": model},
                                             sort_keys=True).encode()).hexdigest()
        from zotero_summarizer.integrations.llm_requests import _generation_endpoint

        base = provider.base_url or "https://api.anthropic.com"
        endpoint = _generation_endpoint(base, "/v1/messages" if provider.type == "anthropic" else "/chat/completions")

        def expected():
            self.require_active()
            if provider.is_local and local_request_fence(configuration):
                # A previous coherent invocation is allowed; an unresolved one is not.
                raise RuntimeError("Unresolved local request ownership prevents another request")
            return self.requests.expect(identity, model, endpoint, local=provider.is_local)

        return expected

    def abandon(self, reason: str) -> dict:
        with self._state:
            if not self.finished:
                self.reason = self.reason or reason
                self._aborted.set()
        return self.receipt()

    def _snapshot(self) -> dict:
        completion = self.requests.summary()
        return {"run_id": self.run_id, "callback_started": self.callback_started,
                "callback_state": self.callback_state, "finished": self.finished,
                "ownership": completion["ownership"], "request_completion": completion,
                "reason_code": f"waiter_{self.reason}" if self.reason else "",
                "hold": completion["hold"]}

    def receipt(self) -> dict:
        with self._state:
            return self._snapshot()

    def publish(self, payload: dict, save, *, previous_success=None) -> dict:
        """Acknowledge cancellation or final publication atomically, never both."""
        with self._state:
            if self.reason or self.requests.summary()["fence"]:
                payload.update(ready=False, status="needs_action", modes={}, last_successful_at=previous_success)
            self.finished = True
            payload["attempt"] = self._snapshot()
            save(payload)
            return payload

    def is_abandoned(self) -> bool:
        return self._aborted.is_set()

    def require_active(self) -> None:
        if self.is_abandoned():
            raise RuntimeError("Setup attempt was abandoned; no further request was submitted")

    def run(self, callback, *, timeout: float) -> dict:
        """Use the existing setup daemon-worker pattern, with a bounded waiter.

        Returning or cancelling a waiter never proves remote termination. A
        timed-out callback retains the worker lock; no successor can overlap it.
        """
        previous_expected = self.requests.summary()["expected"]
        if self.is_abandoned():
            return self._failure(start=previous_expected)
        if not _WORKER.acquire(blocking=False):
            return {"status": "fail", "reason_code": "setup_load_busy", "attempted": False,
                    "ownership": "not_submitted", "detail": "Previous setup callback is still running; wait for it to return."}
        done, result, errors = Event(), {}, []

        def work():
            token = _CURRENT.set(self)
            try:
                with self._state:
                    if self._aborted.is_set():
                        return
                    self.callback_started = True
                    self.callback_state = "active"
                result.update(callback())
            except Exception as exc:  # noqa: BLE001 — worker diagnostic boundary
                result.update(status="fail", reason_code="verification_failed",
                              detail=f"{type(exc).__name__}: Verification failed; request completion is separately observed.")
            except BaseException as exc:
                errors.append(exc)
            finally:
                with self._state:
                    self.callback_state = "returned"
                _CURRENT.reset(token)
                _WORKER.release()
                done.set()

        try:
            Thread(target=work, name="setup-verification", daemon=True).start()
        except RuntimeError:
            _WORKER.release()
            raise
        deadline = monotonic() + timeout
        while not done.wait(min(0.05, max(0, deadline - monotonic()))):
            if self.is_abandoned():
                return self._failure(start=previous_expected)
            if monotonic() >= deadline:
                self.abandon("timeout")
                return self._failure(start=previous_expected)
        if errors:
            raise errors[0]
        if self.is_abandoned():
            return self._failure(start=previous_expected)
        completion = self.requests.summary(start=previous_expected)
        if result.get("status") == "operational" and not completion["expected"]:
            result.update(status="fail", reason_code="request_inventory_missing",
                          detail="Callback return is not evidence of an owned native request.")
        if completion["expected"] and not completion["successful"]:
            result.update(status="fail", reason_code="request_inventory_incomplete" if not completion["coherent"]
                          else "request_output_truncated",
                          detail="Verification requires coherent expected, transport-start and exact native terminal evidence.")
        if "id" not in result:
            result.update(ownership=completion["ownership"], request_completion=completion,
                          attempted=bool(completion["started"]))
        return result

    def _failure(self, *, start=0) -> dict:
        completion = self.requests.summary(start=start)
        return {"status": "fail", **self.receipt(), "ownership": completion["ownership"],
                "request_completion": completion, "attempted": bool(completion["started"]), "hold": completion["hold"],
                "detail": f"Setup waiter {self.reason}; callback/remote termination is not established. "
                          "Further checks were stopped; review settings before retrying."}


def checked_rows(settings, selected, runners, attempt):
    """Emit each terminal result so Doctor can persist it before the next check."""
    from zotero_summarizer.services.setup.doctor_environment import _row

    for check_id in selected:
        try:
            if attempt.is_abandoned():
                result = attempt._failure()
            elif check_id in {"runtime_model", "llm_inference", "dry_run", "ml_assets"}:
                result = attempt.run(lambda check_id=check_id: runners[check_id](settings),
                                     timeout=310 if check_id == "ml_assets" else 90)
            else:
                result = runners[check_id](settings)
            if result["status"] == "fail":
                row = _row(check_id, "needs_action", "Verification needs action", result["detail"])
                row["load_receipts"] = [result]
                result = row
        except Exception as exc:  # noqa: BLE001 — diagnostic status boundary
            result = _row(check_id, "needs_action", f"{check_id} failed", f"{type(exc).__name__}: {exc}")
        yield check_id, result
