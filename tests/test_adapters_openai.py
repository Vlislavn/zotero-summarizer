"""Phase 1.8: build_llm passes extra_body only when set (OpenAI compatibility)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from zotero_summarizer.services import _adapters


def test_build_llm_without_extra_body_does_not_pass_kwarg():
    """OpenAI's API rejects unknown params — extra_body must be omitted when None."""
    fake_llm_class = MagicMock()
    with patch.object(_adapters, "_load_onprem", return_value=(fake_llm_class, None)):
        _adapters.build_llm("https://api.openai.com/v1", "gpt-4o-mini", "sk-xxx")
    fake_llm_class.assert_called_once()
    kwargs = fake_llm_class.call_args.kwargs
    assert "extra_body" not in kwargs, f"extra_body leaked into OpenAI build: {kwargs}"
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["openai_api_key"] == "sk-xxx"


def test_build_llm_with_empty_dict_does_not_pass_kwarg():
    """An empty dict is treated as no extra_body."""
    fake_llm_class = MagicMock()
    with patch.object(_adapters, "_load_onprem", return_value=(fake_llm_class, None)):
        _adapters.build_llm("https://x", "m", "k", extra_body={})
    kwargs = fake_llm_class.call_args.kwargs
    assert "extra_body" not in kwargs


def test_build_llm_with_extra_body_forwards_it():
    """vLLM-served reasoning models: extra_body must be passed through unchanged."""
    fake_llm_class = MagicMock()
    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    with patch.object(_adapters, "_load_onprem", return_value=(fake_llm_class, None)):
        _adapters.build_llm("https://localhost:8000/v1", "qwen3:8b", "k", extra_body=extra)
    kwargs = fake_llm_class.call_args.kwargs
    assert kwargs["extra_body"] == extra


def test_build_llm_defaults_temperature_to_zero():
    """No temperature passed → deterministic triage (the previously-hardcoded 0)."""
    fake_llm_class = MagicMock()
    with patch.object(_adapters, "_load_onprem", return_value=(fake_llm_class, None)):
        _adapters.build_llm("https://x", "m", "k")
    assert fake_llm_class.call_args.kwargs["temperature"] == 0


def test_build_llm_threads_explicit_temperature():
    """A configured per-provider temperature must reach the underlying client."""
    fake_llm_class = MagicMock()
    with patch.object(_adapters, "_load_onprem", return_value=(fake_llm_class, None)):
        _adapters.build_llm("https://x", "m", "k", temperature=0.7)
    assert fake_llm_class.call_args.kwargs["temperature"] == 0.7
