"""Provider-aware LLM client construction + operational checks.

See README.md for the domain sketch. The factory resolves a ``ProviderConfig``
(by ``type``) to a live client implementing the ``LLMClient`` protocol; the
operational-check probes each pipeline stage's configured provider on demand.
"""
from __future__ import annotations

from zotero_summarizer.services.llm.factory import (
    build_client_for_provider,
    build_client_for_stage,
)

__all__ = ["build_client_for_provider", "build_client_for_stage"]
