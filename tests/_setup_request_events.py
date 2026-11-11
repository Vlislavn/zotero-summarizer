"""Scripted request-event clients for consumer tests, never native SDK/TCP proof."""
from types import SimpleNamespace
from uuid import uuid4

import httpx

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.setup.load_attempt import current_attempt


def observed_client(provider, model, *, output="ok", owner=None, before_request=None, close=None):
    if owner is None:
        attempt = current_attempt()
        assert attempt is not None
        owner = attempt.bind_client(settings(), provider, model)
    anthropic = provider.type == "anthropic"
    from zotero_summarizer.integrations.llm_requests import _generation_endpoint

    endpoint = _generation_endpoint(provider.base_url or "https://api.anthropic.com",
                                    "/v1/messages" if anthropic else "/chat/completions")

    def prompt(_prompt, **_kwargs):
        if before_request is not None:
            before_request()
        request = owner()
        native_id = uuid4()
        request.native_started(native_id)
        request.transport_started(httpx.Request("POST", endpoint, json={"model": model}))
        request.terminal(model, "end_turn" if anthropic else "stop", native_id=native_id, native_required=True)
        return output

    return SimpleNamespace(prompt=prompt, close=close or (lambda: None))
