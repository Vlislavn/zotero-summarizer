from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

from zotero_summarizer.integrations.llm import InstrumentedLLMClient
from zotero_summarizer.integrations.pdf import OnPremPdfExtractor


def _load_onprem() -> tuple[Any, Any]:
    try:
        from onprem.llm import LLM
        from onprem.ingest.base import load_single_document
        return LLM, load_single_document
    except ImportError:
        configured = os.getenv("ONPREM_PATH", "").strip()
        if not configured:
            raise
        repo_path = Path(configured).expanduser()
        if str(repo_path) not in sys.path:
            sys.path.insert(0, str(repo_path))
        from onprem.llm import LLM
        from onprem.ingest.base import load_single_document
        return LLM, load_single_document


def require_loopback_endpoint(value: str) -> None:
    """Strict literal loopback API admission; not a runtime/hardware approval."""
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    endpoint = urlsplit(value)
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError("local-only generation requires a literal loopback HTTP API")
    host = endpoint.hostname.lower()
    try:
        local = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if not local or endpoint.path.lower().endswith((".gguf", ".bin")):
        raise ValueError("local-only generation requires a literal loopback HTTP API")


def _verification_send(guard, api_key, before_request):
    from zotero_summarizer.integrations.llm_transport import own_headers

    def send(request):
        try:
            if before_request:
                before_request()
            own_headers(request, api_key, raw_response=True)
        except BaseException:
            guard.refuse_before_dispatch()
            raise
        guard.transport_started(request)
    return send


def build_llm(
    model_url: str,
    model_name: str,
    api_key: str | None,
    # PROVISIONAL: 4096 is an operational baseline, not measured (OnPrem.LLM's own default is
    # 512; reasoning providers raise it to 16384). Per-provider override via ProviderConfig.
    # See docs/onprem_llm_evidence.md.
    max_tokens: int = 4096,
    *,
    # temperature 0 = GROUNDED (preserves the previously-hardcoded deterministic-triage value;
    # asserted in test_adapters_openai / test_provider_routing). Override per provider.
    temperature: float = 0,
    extra_body: dict[str, Any] | None = None,
    request_timeout_seconds: float | None = None,
    structured_output: bool = False,
    verification: bool = False,
    before_request: Any = None,
    request_owner: Any = None,
    local_only: bool = False,
    no_auth: bool = False,
) -> InstrumentedLLMClient:
    from zotero_summarizer.integrations.llm_callbacks import CompletionGuard

    if local_only:
        if verification:
            raise ValueError("local-only inference and Setup verification are separate operations")
        require_loopback_endpoint(model_url)
    if verification:
        from urllib.parse import urlsplit

        endpoint = urlsplit(model_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.path.lower().endswith((".gguf", ".bin"))):
            raise ValueError("Verification requires an HTTP API, not model weights")
    if verification and request_owner is None:
        from functools import partial
        from uuid import uuid4
        from zotero_summarizer.integrations.llm_requests import RequestInventory, _generation_endpoint

        inventory = RequestInventory(uuid4().hex, before_start=before_request)
        request_owner = partial(inventory.expect, "direct-verification", model_name,
                                _generation_endpoint(model_url, "/chat/completions"), local=False)
    if no_auth and request_timeout_seconds is None:
        raise ValueError("Declared no-auth requires an explicit request timeout")
    guard = CompletionGuard(expected_model=model_name if verification else None, request_owner=request_owner)
    llm_cls, _ = _load_onprem()
    kwargs: dict[str, Any] = dict(
        model_url=model_url,
        model=model_name,
        openai_api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        mute_stream=True,
        verbose=False,
        callbacks=[guard],
    )
    if extra_body:
        kwargs["extra_body"] = extra_body
    if request_timeout_seconds is not None:
        kwargs["timeout"] = request_timeout_seconds
    transport = None
    if verification:
        from zotero_summarizer.integrations.llm_requests import verification_http_client

        transport = verification_http_client(_verification_send(guard, api_key, before_request))
        kwargs.update(max_retries=0, cache=False, http_client=transport)
    if local_only:
        from zotero_summarizer.integrations.llm_requests import verification_http_client

        def require_local_request(request):
            require_loopback_endpoint(str(request.url))

        transport = verification_http_client(require_local_request)
        kwargs.update(max_retries=0, cache=False, http_client=transport)
    if no_auth:
        from zotero_summarizer.integrations.llm_transport import no_auth_onprem_options

        if transport is not None:
            transport.close()
        before = _verification_send(guard, None, before_request) if verification else (lambda request: require_loopback_endpoint(str(request.url))) if local_only else None
        transport, owned_options = no_auth_onprem_options(model_url, before, timeout=request_timeout_seconds)
        kwargs.update(owned_options)
        kwargs.pop('http_client', None)
    try:
        inner = llm_cls(**kwargs)
    except BaseException:
        if transport is not None:
            transport.close()
        raise
    def rebuild(larger: int) -> InstrumentedLLMClient:
        return build_llm(model_url, model_name, api_key, larger, temperature=temperature, extra_body=extra_body,
                         request_timeout_seconds=request_timeout_seconds, structured_output=structured_output,
                         local_only=local_only, no_auth=no_auth)

    return InstrumentedLLMClient(inner, structured_output=structured_output,
                                 verification_guard=guard if verification else None,
                                 owned_transport=transport, before_request=before_request,
                                 max_tokens=max_tokens, rebuild=None if verification else rebuild)


def build_pdf_extractor() -> OnPremPdfExtractor:
    _, load_single_document = _load_onprem()
    return OnPremPdfExtractor(load_single_document)
