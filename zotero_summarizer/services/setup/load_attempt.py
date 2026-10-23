"""Bound the setup waiter; only its synchronous callback releases exclusion."""
from contextvars import ContextVar
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

    def abandon(self, reason: str) -> dict:
        with self._state:
            if not self.finished:
                self.reason = self.reason or reason
                self._aborted.set()
        return self.receipt()

    def _snapshot(self) -> dict:
        return {"run_id": self.run_id, "callback_started": self.callback_started,
                "callback_state": self.callback_state, "finished": self.finished,
                "ownership": ("not_submitted" if not self.callback_started else
                              "unresolved" if self.reason or self.callback_state != "returned" else "returned"),
                "reason_code": f"waiter_{self.reason}" if self.reason else "",
                "hold": bool(self.reason and self.callback_started)}

    def receipt(self) -> dict:
        with self._state:
            return self._snapshot()

    def publish(self, payload: dict, save, *, previous_success=None) -> dict:
        """Acknowledge cancellation or final publication atomically, never both."""
        with self._state:
            if self.reason:
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
        if self.is_abandoned():
            return self._failure()
        if not _WORKER.acquire(blocking=False):
            return {"status": "fail", "reason_code": "setup_load_busy", "attempted": False,
                    "ownership": "not_submitted", "detail": "Previous setup callback is still running; wait for it to return."}
        done, result = Event(), {}

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
                result.update(status="fail", ownership="unresolved",
                              detail=f"{type(exc).__name__}: {exc}. Callback failure does not establish request completion.")
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
                return self._failure()
            if monotonic() >= deadline:
                self.abandon("timeout")
                return self._failure()
        return self._failure() if self.is_abandoned() else result

    def _failure(self) -> dict:
        return {"status": "fail", **self.receipt(),
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
