"""Content identity for cached deep reviews."""
from __future__ import annotations

import hashlib
import json
import re
import types
from functools import lru_cache
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.runtime import memoized
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.library._review_attempt import file_identity


def _source_sha(path: str) -> str:
    if not path:
        return ""
    source = Path(path)
    if not source.is_file():
        return "missing"
    return memoized(("source_sha", path), lambda: file_identity(source)['sha256'])


@lru_cache(maxsize=1)
def _digest_schema() -> dict[str, Any]:
    return PaperDigest.model_json_schema()


def _text_fragments(value: Any, depth: int = 0, scalars: bool = False) -> list[str]:
    """Prompt/contract TEXT reachable from a module constant: template strings,
    checklist/rubric items and regex-contract patterns — not code or short keys.
    ``scalars`` also keeps numbers, flags and short constants, for contracts whose
    semantics live in caps/limits/field names rather than in prose."""
    if depth > 5:
        return []
    if isinstance(value, str):
        return [value] if scalars or len(value) >= 20 else []
    if isinstance(value, re.Pattern):
        return [value.pattern]
    if isinstance(value, (bool, int, float)):
        return [repr(value)] if scalars else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [fragment for item in value for fragment in _text_fragments(item, depth + 1, scalars)]
    if isinstance(value, dict):
        return [fragment for key, item in value.items()
                for fragment in (_text_fragments(key, depth + 1, scalars)
                                 + _text_fragments(item, depth + 1, scalars))]
    return []


def _module_prompt_text(module: Any, *, scalars: bool = False) -> str:
    fragments: list[str] = []
    for name in sorted(vars(module)):
        if name.startswith("__"):
            continue
        value = vars(module)[name]
        if isinstance(value, (types.FunctionType, types.ModuleType, type)):
            continue
        fragments.extend(f"{name}={fragment}" for fragment in _text_fragments(value, scalars=scalars))
    return hashlib.sha256("\x00".join(fragments).encode()).hexdigest()


def contract_text_digests(modules: tuple[Any, ...], *, scalars: bool = False) -> dict[str, str]:
    """``{module: hash of its contract text/constants}`` — the receipt-side twin of
    ``_prompt_contract_text``: a logic-only edit keeps the receipt valid."""
    return {module.__name__.rsplit(".", 1)[-1]: _module_prompt_text(module, scalars=scalars)
            for module in modules}


@lru_cache(maxsize=1)
def _prompt_contract_text() -> dict[str, str]:
    """Hash the prompt/contract TEXT the review pipeline sends (templates, rubrics,
    checklists, regex contracts) — NOT whole source files. Editing prompt prose
    restales reviews; refactoring logic that leaves the text unchanged does not."""
    from zotero_summarizer.integrations import pdf as analysis_extractor
    from zotero_summarizer.services.library import (
        _deep_review_layers, _feed_assessment, _field_repair, _map_reduce, _paper_goal_summaries,
        _paper_section_summaries, _paper_type_checklists, _quality_prompts,
        _source_admission, _auth_envelope, paper_type, quality_eval, quality_review,
    )
    from zotero_summarizer.services.library.review_fleet import propose

    modules = (
        analysis_extractor, _deep_review_layers, _feed_assessment, _field_repair, _map_reduce, _paper_goal_summaries,
        _paper_section_summaries, _paper_type_checklists, _quality_prompts,
        _source_admission, _auth_envelope, paper_type, quality_eval, quality_review, propose,
    )
    return contract_text_digests(modules)


# Provider knobs that only spread the same prompt over more/fewer slots. They never
# change what the model is asked, so tuning them must not stale every review (#142).
_SCHEDULING_ONLY_PROVIDER_FIELDS = ("max_sub_concurrency", "keep_alive")


def _resolved_stage_payload(config: Any) -> dict[str, Any]:
    resolved = resolve_stage(config.llm_routing, "deep_review")
    payload = resolved.model_dump(mode="json")
    provider = payload.get("provider")
    if isinstance(provider, dict):
        for field in _SCHEDULING_ONLY_PROVIDER_FIELDS:
            provider.pop(field, None)
    return payload


def build_review_identity(
    *, config: Any, pdf_path: str, source_kind: str, focus_prompt: str,
) -> dict[str, str]:
    # Identity tracks ONLY inputs that change review content: the resolved review
    # model/thinking/timeouts, research goals (the goal layer), the output schema,
    # the focus prompt, the user-overridable prompt templates and the prompt/contract
    # TEXT. Ranking/scheduling/card knobs and unrelated source edits deliberately stay
    # out, so they never stale a review.
    payload = {
        "resolved_stage": _resolved_stage_payload(config),
        "research_goals": list(config.research_goals),
        "focus_prompt": focus_prompt,
        "timeout_seconds": settings().summary_timeout_seconds,
        "schema": _digest_schema(),
        "prompt_overrides": config.prompts.model_dump(mode="json"),
        "prompt_contract": _prompt_contract_text(),
    }
    if getattr(config, "review_depth", "thorough") != "thorough":
        payload["review_depth"] = config.review_depth  # absent for thorough: existing reviews stay current
    generation = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {
        "generation_sha256": generation,
        "source_sha256": _source_sha(pdf_path),
        "source_kind": source_kind,
        "source_path": pdf_path,
        "focus_prompt": focus_prompt,
    }


def _library_pdf_paths(app: Any, item_keys: list[str]) -> dict[str, str]:
    from zotero_summarizer.services.zotero.zotero import get_library_reader

    paths = get_library_reader(app).get_pdf_paths(item_keys)
    return {key: paths.get(key) or "" for key in item_keys}


def prime_pdf_paths(entries: dict[str, dict[str, Any]]) -> None:
    """Resolve every library-bound cached review's PDF path in one query for the current
    ``request_memo`` pass; on any failure the per-item lookup runs as before."""
    keys = [key for key, entry in entries.items()
            if isinstance(entry.get("review_identity"), dict) and entry["review_identity"].get("source_kind") != "override"]
    try:
        paths = _library_pdf_paths(state(), keys) if keys else {}
    except (APIError, AttributeError, OSError, RuntimeError, ValueError):
        return
    for key, path in paths.items():
        memoized(("pdf_path", key), lambda path=path: path)


def current_review_identity(item_key: str, stored: dict[str, Any], *, config: Any = None) -> dict[str, str]:
    app = state() if config is None else None
    config = app.app_state.config if config is None else config
    source_kind = str(stored.get("source_kind") or "")
    pdf_path = str(stored.get("source_path") or "")
    if source_kind != "override":
        if app is None:
            raise ValueError("cached feed review requires an acquired original PDF binding")
        pdf_path = memoized(("pdf_path", item_key), lambda: _library_pdf_paths(app, [item_key])[item_key])
        source_kind = "library" if pdf_path else "none"
    return build_review_identity(
        config=config, pdf_path=pdf_path, source_kind=source_kind,
        focus_prompt=str(stored.get("focus_prompt") or ""),
    )


INTERPRETATION_SCHEMA_MARKER = "interpretation-digest-response-v1"


def _condition_matches(stored: dict[str, Any], interpretation_schema: str | None) -> bool:
    if interpretation_schema not in (None, INTERPRETATION_SCHEMA_MARKER):
        return False
    if interpretation_schema is None:
        return "interpretation_schema" not in stored
    return stored.get("interpretation_schema") == INTERPRETATION_SCHEMA_MARKER
