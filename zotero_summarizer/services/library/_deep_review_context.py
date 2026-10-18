"""Per-run review dependency resolution, kept separate from per-item coordination."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def review_entry(
    item: dict, review_identity: dict, pdf_path: str, digest: dict | None, layers: tuple,
    reading_policy: tuple, *, verification_audit: dict | None = None,
    note_delivery: tuple = (False, None), provenance: dict | None = None, reviewed_at: str | None = None,
) -> dict:
    """Assemble the unchanged base record; note execution remains in deep_review."""
    from zotero_summarizer.services._common import now_iso_z
    from zotero_summarizer.services.library._review_cache import REVIEW_CONTRACT_VERSION

    quality, goals, paper_type, section_overlay, code_link = layers
    model_read_decision, reading_policy_flags = reading_policy
    note_written, note_error = note_delivery
    return {
        "review_contract_version": REVIEW_CONTRACT_VERSION,
        "review_identity": review_identity,
        "provenance": provenance or {},
        "digest": digest,
        "verification_audit": verification_audit or None,
        "quality": quality,
        "goal_summaries": goals,
        "paper_type": paper_type,
        "section_overlay": section_overlay,
        "code_link": code_link,
        "model_read_decision": model_read_decision,
        "reading_policy_flags": reading_policy_flags,
        "needs_pdf": not bool(pdf_path),
        "needs_login": False,
        "login_url": "",
        "gate_relevance": item.get("gate_relevance"),
        "reviewed_at": reviewed_at if reviewed_at is not None else now_iso_z(),
        "zotero_note_written": note_written,
        "zotero_note_error": note_error,
    }


def resolve_context(
    app: Any,
    *,
    prestige_loader: Callable[[], tuple[dict[str, Any], float | None]],
    concurrency_resolver: Callable[[Any], int],
    quality_review: Any,
    digest_model: Any,
    review_contract_version: int,
    reader: Any = None,
) -> dict[str, Any]:
    """Resolve the existing default context; facade dependencies are captured per call."""
    config = app.app_state.config
    cfg = config.quality_review
    extractor = getattr(app, "pdf_extractor", None)
    # Zotero-optional: live Zotero reader when present, else the app library over
    # kept feed papers (PDF acquired into the local cache by _pdf_acquire).
    from zotero_summarizer.services.zotero.zotero import get_library_reader

    reader = reader or get_library_reader(app)
    from zotero_summarizer.models.providers import resolve_stage

    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider = app.resolve_stage_provider("deep_review")
    prestige_scores, prestige_floor_value = prestige_loader()
    return {
        "reader": reader,
        "config": config,
        "extractor": extractor,
        "quality_enabled": bool(cfg.enabled and extractor is not None),
        "llm": app.resolve_stage_client("deep_review"),
        "llm_digest": app.resolve_stage_client("deep_review", enable_thinking=provider.thinking_on),  # DIGEST reasons unless provider thinking_effort=off
        "llm_map": app.resolve_stage_client("feed") if cfg.chunk_strategy == "map_reduce" else None,
        "prestige_scores": prestige_scores,
        "prestige_floor_value": prestige_floor_value,
        "lean_tier": bool(getattr(provider, "lean_deep_review", False)),  # ollama=prefill-bound→lean; keyed on flag, not is_local
        # Sub-call concurrency within one paper (local→serial, remote→capped); shared with verify-deep-review.
        "sub_concurrency": concurrency_resolver(provider),
        # Decoder-level structured output: JSON Schema for PaperDigest when the provider supports it
        # (vLLM-style: logit constraint + reasoning survives, measured 2026-06-27); None → prompt-level fallback.
        "response_format": quality_review.build_response_format(digest_model) if getattr(provider, "structured_output", False) else None,
        "provenance": {
            "provider": getattr(provider, "name", resolved.provider.name),
            "model": resolved.model,
            "prompt_schema_version": review_contract_version,
        },
        "_provider": provider,
    }
