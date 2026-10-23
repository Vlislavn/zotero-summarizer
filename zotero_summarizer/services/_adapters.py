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


def build_llm(
    model_url: str,
    model_name: str,
    api_key: str,
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
) -> InstrumentedLLMClient:
    from zotero_summarizer.integrations.llm_callbacks import CompletionGuard

    if verification:
        from urllib.parse import urlsplit

        endpoint = urlsplit(model_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.path.lower().endswith((".gguf", ".bin"))):
            raise ValueError("Verification requires an HTTP API, not model weights")
    guard = CompletionGuard(expected_model=model_name if verification else None)
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
        import httpx

        transport = httpx.Client(follow_redirects=False, trust_env=False)
        kwargs.update(max_retries=0, cache=False, http_client=transport)
    try:
        inner = llm_cls(**kwargs)
    except BaseException:
        if transport is not None:
            transport.close()
        raise
    return InstrumentedLLMClient(inner, structured_output=structured_output,
                                 verification_guard=guard if verification else None,
                                 owned_transport=transport, before_request=before_request)


def build_pdf_extractor() -> OnPremPdfExtractor:
    _, load_single_document = _load_onprem()
    return OnPremPdfExtractor(load_single_document)
