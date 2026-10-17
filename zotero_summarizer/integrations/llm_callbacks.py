"""Observe completion metadata before OnPrem reduces responses to plain text."""

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .llm import LOGGER, LLMRequestObserver


class LLMOutputTruncated(RuntimeError):
    """The provider explicitly reported an exhausted output budget."""


class CompletionGuard(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, *, request_observer: LLMRequestObserver | None = None) -> None:
        self._request_observer = request_observer

    def on_chat_model_start(self, _serialized: dict, _messages: list, **kwargs: Any) -> None:
        if self._request_observer is not None:
            self._request_observer.request_started(kwargs.get("run_id"))

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        if self._request_observer is not None:
            reason = None
            if len(response.generations) == 1 and len(response.generations[0]) == 1:
                info = response.generations[0][0].generation_info or {}
                reason = info.get("finish_reason")
            self._request_observer.request_finished(kwargs.get("run_id"), reason)
        usage = (response.llm_output or {}).get("token_usage", {})
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
