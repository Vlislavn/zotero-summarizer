"""Native Anthropic messages-API client implementing the LLMClient protocol.

A drop-in alternative to ``InstrumentedLLMClient`` (the OpenAI-compatible
wrapper) for providers of ``type: anthropic``. Exposes the same two-method
surface every triage/quality call site relies on:

- ``prompt(text) -> str`` — raw text completion.
- ``pydantic_prompt(text, pydantic_model) -> instance`` — structured output,
  validated against ``pydantic_model`` via the SDK's ``messages.parse`` helper.

Layering: this is an external-system client, so it lives in ``integrations/``
and imports nothing from ``services/``/``api/``. The ``anthropic`` SDK is a lazy
import (mirroring the OnPrem lazy-load in ``services/_adapters.py``) so an
environment without the package only fails when an Anthropic provider is
actually used — keeping startup robust.

Notes for Opus 4.7/4.8: ``temperature``/``top_p``/``top_k`` are removed and
return 400, so this client does NOT send a temperature — determinism is not
tunable on those models. Thinking is left off (the default when omitted); a
concise system instruction keeps the response to just the requested content.
"""
from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger("zotero_summarizer.llm")

# Stable instruction sent as the (cacheable) system block. Kept terse so models
# that omit thinking don't pad the response with reasoning prose.
_SYSTEM = (
    "You are a precise research-paper triage assistant. Follow the user's "
    "instructions exactly and return only the content they ask for, with no "
    "preamble or commentary."
)

# Generous default request timeout (seconds): triage/quality calls run in
# background workers and can take minutes on reasoning models. A roomy timeout
# also suppresses the SDK's large-max_tokens non-streaming guard.
_DEFAULT_TIMEOUT_SECS = 600.0


class AnthropicLLMClient:
    """LLMClient over the native Anthropic messages API (see module docstring)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECS,
    ) -> None:
        import anthropic  # lazy: optional dependency

        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=(base_url or None),
            timeout=timeout,
        )
        self._model = model
        self._max_tokens = max_tokens

    @staticmethod
    def _system_blocks() -> list[dict[str, Any]]:
        return [{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}]

    def _log_usage(self, kind: str, usage: Any) -> None:
        LOGGER.info(
            "Anthropic %s input_tokens=%s output_tokens=%s cache_read=%s",
            kind,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
            getattr(usage, "cache_read_input_tokens", 0),
        )

    def prompt(self, prompt: str, **kwargs: Any) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system_blocks(),
            messages=[{"role": "user", "content": prompt}],
        )
        self._log_usage("prompt", resp.usage)
        return "".join(block.text for block in resp.content if block.type == "text")

    def pydantic_prompt(self, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        resp = self._client.messages.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system_blocks(),
            messages=[{"role": "user", "content": prompt}],
            output_format=pydantic_model,
        )
        self._log_usage("pydantic_prompt", resp.usage)
        parsed = resp.parsed_output
        if parsed is None:
            # A refusal or truncation left no structured output. Fail loudly —
            # callers expect a validated instance, never None.
            raise RuntimeError(
                f"Anthropic structured output was empty for {pydantic_model.__name__} "
                f"(stop_reason={resp.stop_reason!r})"
            )
        return parsed
