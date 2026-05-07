from __future__ import annotations

import logging
from typing import Any, Protocol

LOGGER = logging.getLogger("zotero_summarizer.llm")


class LLMClient(Protocol):
    def prompt(self, prompt: str, **kwargs: Any) -> Any:
        ...

    def pydantic_prompt(self, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        ...


class InstrumentedLLMClient:
    """Provider-neutral logging wrapper for OpenAI-compatible LLM clients."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner

    @staticmethod
    def _to_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("output_text", "text", "result", "summary"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    return candidate
        return str(value)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 4

    def prompt(self, prompt: str, **kwargs: Any) -> Any:
        result = self._inner.prompt(prompt, **kwargs)
        LOGGER.info(
            "LLM prompt input_tokens≈%d output_tokens≈%d",
            self._estimate_tokens(prompt),
            self._estimate_tokens(self._to_text(result)),
        )
        return result

    def pydantic_prompt(self, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        result = self._inner.pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)
        LOGGER.info(
            "LLM pydantic_prompt input_tokens≈%d output_tokens≈%d",
            self._estimate_tokens(prompt),
            self._estimate_tokens(self._to_text(result)),
        )
        return result
