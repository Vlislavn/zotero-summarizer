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
        if configured:
            repo_path = Path(configured).expanduser()
        else:
            repo_path = Path(__file__).resolve().parents[2] / "from GH" / "onprem"
        if str(repo_path) not in sys.path:
            sys.path.insert(0, str(repo_path))
        from onprem.llm import LLM
        from onprem.ingest.base import load_single_document
        return LLM, load_single_document


def build_llm(
    model_url: str,
    model_name: str,
    api_key: str,
    max_tokens: int = 4096,
    *,
    extra_body: dict[str, Any] | None = None,
) -> InstrumentedLLMClient:
    llm_cls, _ = _load_onprem()
    kwargs: dict[str, Any] = dict(
        model_url=model_url,
        model=model_name,
        openai_api_key=api_key,
        temperature=0,
        max_tokens=max_tokens,
        mute_stream=True,
        verbose=False,
    )
    if extra_body:
        kwargs["extra_body"] = extra_body
    return InstrumentedLLMClient(llm_cls(**kwargs))


def build_triage_llm(model: str = "sota") -> InstrumentedLLMClient:
    """Build the LLM client for the whole-backlog triage drain, pointed at
    the custom provider configured via ``CUSTOM_BASE_URL`` +
    ``CUSTOM_API_KEY`` in ``.env``. ``model`` defaults to the endpoint's
    ``sota`` alias.

    Raises if the provider isn't configured — the caller asked for this
    provider explicitly, so a missing key/URL is a hard error, not a
    silent fallback to the local default. ``extra_body`` is dropped (the
    OnPrem-specific kwargs are rejected by external OpenAI-compatible
    endpoints — same rule as the ``goldenset classify-llm`` CLI path).
    """
    base = os.environ.get("CUSTOM_BASE_URL", "").strip()
    key = os.environ.get("CUSTOM_API_KEY", "").strip()
    if not base:
        raise RuntimeError(
            "CUSTOM_BASE_URL is not set; add the custom provider base URL to .env"
        )
    if not key:
        raise RuntimeError(
            "CUSTOM_API_KEY is not set; add the custom provider API key to .env"
        )
    # `sota` is a reasoning model: at max_tokens=2048 the thinking phase
    # consumed the entire budget and the response came back EMPTY, so every
    # refine parse failed and no survivor ever scored. Verified empirically:
    # 2048 -> 0 chars (fail), 8192 -> valid ~3.6k JSON (ok). max_tokens only
    # caps generation (the model stops when the JSON is complete), so a roomy
    # budget is free latency-wise; 16384 leaves ample headroom for reasoning.
    return build_llm(base, model, key, max_tokens=16384, extra_body=None)


def build_pdf_extractor() -> OnPremPdfExtractor:
    _, load_single_document = _load_onprem()
    return OnPremPdfExtractor(load_single_document)
