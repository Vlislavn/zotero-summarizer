"""Observe completion metadata before OnPrem reduces responses to plain text."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

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

    def __init__(self, *, expected_model: str | None = None):
        self.expected_model = expected_model
        self.completed = False

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        if self.expected_model is not None:
            if len(response.generations) != 1 or len(response.generations[0]) != 1:
                raise ValueError("Verification requires one observed response")
            generation = response.generations[0][0]
            metadata = getattr(getattr(generation, "message", None), "response_metadata", {}) or {}
            observed = metadata.get("model_name") or metadata.get("model") or (response.llm_output or {}).get("model_name")
            reason = (generation.generation_info or {}).get("finish_reason")
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
