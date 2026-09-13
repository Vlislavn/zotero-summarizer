"""The installed LangChain callback path must propagate truncation, not swallow it."""

import httpx
import pytest
from langchain_openai import ChatOpenAI

from zotero_summarizer.integrations.llm_callbacks import CompletionGuard


@pytest.mark.parametrize("content", [None, '{"ok":'])
def test_completion_guard_rejects_truncation_but_accepts_complete_response(content):
    reason = "length"

    def respond(request):
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "finish_reason": reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 16, "total_tokens": 26},
        })

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        llm = ChatOpenAI(model="test", api_key="test", http_client=client,
                         callbacks=[CompletionGuard()], max_retries=0)
        with pytest.raises(RuntimeError, match="output token limit"):
            llm.invoke("Return JSON")
        reason, content = "stop", '{"ok":true}'
        assert llm.invoke("Return JSON").content == content
