"""Observe completion metadata before OnPrem reduces responses to plain text."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from threading import Lock

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .llm import LOGGER

_OBSERVER = ContextVar('completion_observer', default=None)


@contextmanager
def observe_completions(consume):
    token = _OBSERVER.set(consume)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


class LLMOutputTruncated(RuntimeError):
    """The provider explicitly reported an exhausted output budget."""


class CompletionGuard(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, *, expected_model: str | None = None, request_owner=None):
        self.expected_model = expected_model
        self.completed = False
        self._request_owner = request_owner
        self._request = None
        self._invocation = Lock()

    def begin_request(self):
        if not self._invocation.acquire(blocking=False):
            raise RuntimeError("Verification client already has an owned invocation")
        try:
            self.completed = False
            self._request = self._request_owner() if self._request_owner is not None else None
        except BaseException:
            self._invocation.release()
            raise

    def finish_request(self):
        if not self.completed:
            raise ValueError("Verification received no observed completion metadata")

    def end_request(self):
        self._request = None
        self._invocation.release()

    def transport_started(self, request):
        if self._request is None:
            raise ValueError("Verification transport has no expected request")
        self._request.transport_started(request)

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if self._request is not None:
            self._request.native_started(run_id)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        if self.expected_model is not None:
            if len(response.generations) != 1 or len(response.generations[0]) != 1:
                raise ValueError("Verification requires one observed response")
            generation = response.generations[0][0]
            metadata = getattr(getattr(generation, "message", None), "response_metadata", {}) or {}
            observed = metadata.get("model_name") or metadata.get("model") or (response.llm_output or {}).get("model_name")
            reason = (generation.generation_info or {}).get("finish_reason")
            if self._request is not None:
                self._request.terminal(observed, reason, native_id=kwargs.get("run_id"), native_required=True)
            if reason == "length":
                raise LLMOutputTruncated("LLM response exhausted its output token limit (finish_reason=length)")
            if observed != self.expected_model or reason != "stop":
                raise ValueError("Verification model identity or successful terminal reason was not established")
            self.completed = True
        usage = (response.llm_output or {}).get("token_usage", {})
        observer = _OBSERVER.get()
        if observer is not None:
            observer(response)
        for generations in response.generations:
            for generation in generations:
                reason = (generation.generation_info or {}).get("finish_reason")
                LOGGER.info(
                    "LLM completion finish_reason=%s prompt_tokens=%s completion_tokens=%s",
                    reason, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                )
                if reason == "length":
                    raise LLMOutputTruncated(
                        "LLM response exhausted its output token limit (finish_reason=length). "
                        "Reduce thinking effort or increase the provider's max_tokens; "
                        "the incomplete response was not accepted."
                    )
