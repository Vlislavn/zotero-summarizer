"""Observe completion metadata before OnPrem reduces responses to plain text."""

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .llm import LOGGER


class CompletionGuard(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage", {})
        for generations in response.generations:
            for generation in generations:
                reason = (generation.generation_info or {}).get("finish_reason")
                LOGGER.info(
                    "LLM completion finish_reason=%s prompt_tokens=%s completion_tokens=%s",
                    reason, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                )
                if reason == "length":
                    raise RuntimeError(
                        "LLM response exhausted its output token limit (finish_reason=length). "
                        "Reduce thinking effort or increase the provider's max_tokens; "
                        "the incomplete response was not accepted."
                    )
