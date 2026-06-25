"""Thinking-effort → wire-param translation (services.llm.thinking). Pure, no
network: maps an off/low/medium/high level onto each backend dialect."""
from __future__ import annotations

import pytest

from zotero_summarizer.services.llm.thinking import (
    apply_effort_openai,
    effort_to_anthropic_budget,
)


# --- anthropic budget ------------------------------------------------------

def test_anthropic_budget_off_and_none_disable_thinking():
    assert effort_to_anthropic_budget(None) is None
    assert effort_to_anthropic_budget("off") is None


def test_anthropic_budget_levels_increase():
    low = effort_to_anthropic_budget("low")
    med = effort_to_anthropic_budget("medium")
    high = effort_to_anthropic_budget("high")
    assert 0 < low < med < high


# --- openai dialects -------------------------------------------------------

def test_openai_none_effort_is_noop():
    base = {"chat_template_kwargs": {"enable_thinking": False}, "keep": 1}
    assert apply_effort_openai(None, base) is base  # untouched, same object
    assert apply_effort_openai(None, None) is None


def test_openai_plain_dialect_sets_reasoning_effort():
    # No chat_template_kwargs → real OpenAI / OpenRouter reasoning dialect.
    out = apply_effort_openai("high", None)
    assert out == {"reasoning_effort": "high"}
    out2 = apply_effort_openai("medium", {"keep": 1})
    assert out2 == {"keep": 1, "reasoning_effort": "medium"}


def test_openai_plain_dialect_off_omits_reasoning_effort():
    # off must not force reasoning; an empty result collapses back to None.
    assert apply_effort_openai("off", None) is None
    assert apply_effort_openai("off", {"reasoning_effort": "high"}) is None
    assert apply_effort_openai("off", {"keep": 1}) == {"keep": 1}


def test_openai_chat_template_dialect_toggles_enable_thinking():
    # vLLM/qwen dialect: graded levels collapse to enable_thinking on/off.
    base = {"chat_template_kwargs": {"enable_thinking": False}, "keep": 1}
    on = apply_effort_openai("low", base)
    assert on["chat_template_kwargs"]["enable_thinking"] is True
    assert on["keep"] == 1
    off = apply_effort_openai("off", base)
    assert off["chat_template_kwargs"]["enable_thinking"] is False
    # source dict never mutated
    assert base["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_openai_chat_template_any_level_enables(effort):
    base = {"chat_template_kwargs": {"enable_thinking": False}}
    out = apply_effort_openai(effort, base)
    assert out["chat_template_kwargs"]["enable_thinking"] is True
