"""Translate a provider's ``thinking_effort`` level into the wire params each
backend dialect actually understands. Pure functions (no env, no network, no
client) so they live next to the factory and are trivially unit-tested.

A single ``off|low|medium|high`` knob can't map identically across the
OpenAI-compatible zoo, so the dialect is **inferred from what the provider
already declares** — no extra "dialect" config field:

- ``anthropic``  → a thinking *budget* (token count). Truly graded.
- OpenAI-compatible with ``extra_body.chat_template_kwargs`` (vLLM / qwen3) →
  ``enable_thinking`` on/off. This dialect can't express Low≠High, so any
  non-``off`` level just enables thinking (the graded levels collapse — this is
  the documented, non-silent limitation; see the CHANGELOG + UI tooltip).
- plain OpenAI-compatible (real OpenAI / OpenRouter reasoning models) →
  top-level ``reasoning_effort`` (carried in ``extra_body``).

``effort is None`` (the default) injects nothing — byte-identical to the
pre-feature behavior, so existing configs and deep_review's per-call
``enable_thinking`` override keep working unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Anthropic extended-thinking budget per effort level (tokens). The adapter
# clamps max_tokens up to budget+slack since the API requires max_tokens > budget.
# PROVISIONAL (unmeasured — see docs/onprem_llm_evidence.md): a geometric 2K/8K/16K
# ladder, no Anthropic-doc citation or eval behind the exact values. Only used when a
# provider sets thinking_effort (default None → never injected).
_ANTHROPIC_BUDGET: dict[str, int] = {"low": 2048, "medium": 8192, "high": 16384}


def effort_to_anthropic_budget(effort: Optional[str]) -> Optional[int]:
    """Map an effort level to an Anthropic thinking budget (tokens).

    ``None`` and ``"off"`` → ``None`` (thinking disabled — the current default).
    """
    if not effort or effort == "off":
        return None
    return _ANTHROPIC_BUDGET[effort]


def apply_effort_openai(
    effort: Optional[str], base_extra_body: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return a NEW ``extra_body`` with the effort applied to the right dialect.

    Never mutates ``base_extra_body`` (the caller's stored provider config). A
    ``None`` effort is a no-op that returns ``base_extra_body`` untouched.
    """
    if effort is None:
        return base_extra_body

    extra: Dict[str, Any] = dict(base_extra_body) if base_extra_body else {}

    if "chat_template_kwargs" in extra:
        # vLLM/qwen dialect: on/off only — graded levels collapse to enable_thinking.
        ctk = dict(extra["chat_template_kwargs"])
        ctk["enable_thinking"] = effort != "off"
        extra["chat_template_kwargs"] = ctk
    elif effort == "off":
        # plain dialect, off: don't force reasoning — drop any prior reasoning_effort.
        extra.pop("reasoning_effort", None)
    else:
        extra["reasoning_effort"] = effort

    # Collapse an empty dict back to None so a no-op never injects "extra_body: {}"
    # (real OpenAI rejects unknown/empty keys — same discipline as _override_thinking).
    return extra or None
