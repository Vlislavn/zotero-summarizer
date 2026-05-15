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


def build_pdf_extractor() -> OnPremPdfExtractor:
    _, load_single_document = _load_onprem()
    return OnPremPdfExtractor(load_single_document)
