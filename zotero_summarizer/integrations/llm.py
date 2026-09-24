from __future__ import annotations

import logging
from typing import Any, Protocol

LOGGER = logging.getLogger("zotero_summarizer.llm")


def build_response_format(model: type) -> dict[str, Any]:
    """Decoder-level schema constraint, only for providers declaring support."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__, "schema": model.model_json_schema(), "strict": True,
        },
    }


class LLMClient(Protocol):
    def prompt(self, prompt: str, **kwargs: Any) -> Any:
        ...

    def pydantic_prompt(self, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        ...


class InstrumentedLLMClient:
    """Provider-neutral logging wrapper for OpenAI-compatible LLM clients."""

    def __init__(self, inner: LLMClient, *, structured_output: bool = False) -> None:
        self._inner = inner
        self._structured_output = structured_output

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

    def _log_call(self, label: str, prompt: str, result: Any) -> None:
        LOGGER.info(
            "LLM %s input_tokens≈%d output_tokens≈%d",
            label,
            self._estimate_tokens(prompt),
            self._estimate_tokens(self._to_text(result)),
        )

    def prompt(self, prompt: str, **kwargs: Any) -> Any:
        result = self._inner.prompt(prompt, **kwargs)
        self._log_call("prompt", prompt, result)
        return result

    def pydantic_prompt(self, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        if self._structured_output and "response_format" not in kwargs:
            kwargs["response_format"] = build_response_format(pydantic_model)
        result = self._inner.pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)
        self._log_call("pydantic_prompt", prompt, result)
        return result
