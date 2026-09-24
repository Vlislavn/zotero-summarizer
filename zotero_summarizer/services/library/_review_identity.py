"""Content identity for cached deep reviews."""
from __future__ import annotations

import hashlib
import inspect
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import settings, state


@lru_cache(maxsize=256)
def _file_sha(path: str, mtime_ns: int, size: int) -> str:
    del mtime_ns, size
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _source_sha(path: str) -> str:
    if not path:
        return ""
    source = Path(path)
    if not source.is_file():
        return "missing"
    stat = source.stat()
    return _file_sha(str(source.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1)
def _generation_sources() -> dict[str, str]:
    from zotero_summarizer.services.library import (
        _deep_review_layers, _map_reduce, _paper_goal_summaries,
        _paper_section_summaries, paper_type, quality_eval, quality_review,
    )
    from zotero_summarizer.services.library.review_fleet import propose

    modules = (
        _deep_review_layers, _map_reduce, _paper_goal_summaries,
        _paper_section_summaries, paper_type, quality_eval, quality_review, propose,
    )
    paths = [Path(inspect.getfile(PaperDigest)), *(Path(module.__file__) for module in modules)]
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def build_review_identity(
    *, config: Any, pdf_path: str, source_kind: str, focus_prompt: str,
) -> dict[str, str]:
    resolved = resolve_stage(config.llm_routing, "deep_review")
    payload = {
        "config": config.model_dump(mode="json"),
        "resolved_stage": resolved.model_dump(mode="json"),
        "focus_prompt": focus_prompt,
        "timeout_seconds": settings().summary_timeout_seconds,
        "schema": PaperDigest.model_json_schema(),
        "sources": _generation_sources(),
    }
    generation = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {
        "generation_sha256": generation,
        "source_sha256": _source_sha(pdf_path),
        "source_kind": source_kind,
        "source_path": pdf_path,
        "focus_prompt": focus_prompt,
    }


def current_review_identity(item_key: str, stored: dict[str, Any]) -> dict[str, str]:
    app = state()
    config = app.app_state.config
    source_kind = str(stored.get("source_kind") or "")
    pdf_path = str(stored.get("source_path") or "")
    if source_kind != "override":
        from zotero_summarizer.services.zotero.zotero import get_library_reader

        detail = get_library_reader(app).get_item_detail(item_key) or {}
        pdf_path = str(detail.get("pdf_path") or "")
        source_kind = "library" if pdf_path else "none"
    return build_review_identity(
        config=config, pdf_path=pdf_path, source_kind=source_kind,
        focus_prompt=str(stored.get("focus_prompt") or ""),
    )
