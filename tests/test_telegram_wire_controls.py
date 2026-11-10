"""Actual client serialization over fake HTTP transport, with no endpoint/model calls."""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from tests.test_telegram_review_corrections import local_context
from zotero_summarizer.services.research_feed import telegram_review
from zotero_summarizer.services.llm import factory
from zotero_summarizer.integrations import llm_requests


class WireReply(BaseModel):
    accepted: bool


@pytest.mark.parametrize("declared", [
    {"max_tokens": 32000, "reasoning_effort": "none"},
    {"max_completion_tokens": 64000, "chat_template_kwargs": {"enable_thinking": False}},
    {"max_tokens": 32000, "max_completion_tokens": 64000, "reasoning_effort": "none"},
])
def test_declared_dialect_and_output_budget_reach_actual_serialized_request(tmp_path, monkeypatch, declared):
    # The suite installs OnPrem stubs; this forked control deliberately loads
    # the installed HTTP client while every send stays on MockTransport.
    for name in list(sys.modules):
        if name == "onprem" or name.startswith("onprem."):
            monkeypatch.delitem(sys.modules, name)
    from onprem.llm import LLM
    from zotero_summarizer.services import _adapters
    monkeypatch.setattr(_adapters, "_load_onprem", lambda: (LLM, None))
    config, profile = local_context(tmp_path)
    provider = config.llm_routing.provider_by_name("local")
    provider.extra_body = {**deepcopy(declared), "unused_large_options": {"context": 999999}}
    provider.thinking_effort = "off"
    provider.structured_output = True
    original = provider.model_dump(mode="json")
    captured = []
    def reply(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "chatcmpl-synthetic", "object": "chat.completion", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"accepted":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
    transport = httpx.MockTransport(reply)
    original_http = llm_requests.verification_http_client
    monkeypatch.setattr(llm_requests, "verification_http_client", lambda hook: original_http(hook, transport=transport))
    monkeypatch.setattr(factory, "resolve_api_key", lambda _provider: "local")
    monkeypatch.setattr(factory, "settings", lambda: SimpleNamespace(summary_timeout_seconds=2))
    resolved = telegram_review.resolve_local(config, profile)
    client = factory.build_client_for_provider(resolved.provider, resolved.model, local_only=True)
    try:
        value = client.pydantic_prompt("Nonprivate synthetic serialization control", WireReply,
                                       attempt_fix=False, truncate_prompt=False)
        assert value.accepted is True
    finally:
        client.close()
    assert len(captured) == 1
    payload = captured[0]
    assert payload["response_format"]["type"] == "json_schema"
    for key in {"max_tokens", "max_completion_tokens"} & declared.keys():
        assert payload[key] == profile.telegram_limits.output_tokens
    if "max_completion_tokens" in payload:
        assert payload["max_completion_tokens"] == profile.telegram_limits.output_tokens
    assert "unused_large_options" not in payload
    if "chat_template_kwargs" in declared:
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning_effort" not in payload
    else:
        assert "chat_template_kwargs" not in payload
        assert payload["reasoning_effort"] == "none"
    assert provider.model_dump(mode="json") == original


def test_undeclared_vllm_options_are_not_manufactured(tmp_path):
    config, profile = local_context(tmp_path)
    resolved = telegram_review.resolve_local(config, profile)
    assert "chat_template_kwargs" not in (resolved.provider.extra_body or {})


