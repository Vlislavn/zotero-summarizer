"""Replace the generic "topic close to papers you saved" row reason with the
nearest kept paper's title ("close to: <title>") when the corpus has one."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.library._row_why import _PHRASE

_TITLE_CHARS = 90
GENERIC_WHY = _PHRASE["semantic_match_specter2"]


def corpus_cache():
    """The corpus ``EmbeddingCache``, or None when the corpus is not enabled
    (callers then keep their gate-only behaviour)."""
    app_state = getattr(get_state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    corpus_cfg = getattr(config, "corpus", None) if config is not None else None
    if corpus_cfg is None or not getattr(corpus_cfg, "enabled", False):
        return None
    from zotero_summarizer.storage.corpus import EmbeddingCache

    return EmbeddingCache(get_settings().corpus_db_path, corpus_cfg.embedding_model)


def attach_nearest_kept(recs: list[dict[str, Any]]) -> None:
    """In place: generic ``why_reason`` → ``close to: <nearest kept title>``;
    unchanged when no neighbour is known (leave the generic sentence)."""
    generic = [r["item_key"] for r in recs if r.get("why_reason") == GENERIC_WHY]
    cache = corpus_cache() if generic else None
    if cache is None:
        return
    try:
        titles = cache.nearest_kept_titles(generic)
    except (OSError, ValueError):  # corpus missing / encoder changed → keep the generic reason
        return
    for rec in recs:
        title = titles.get(rec["item_key"]) if rec.get("why_reason") == GENERIC_WHY else None
        if title:
            short = title if len(title) <= _TITLE_CHARS else title[: _TITLE_CHARS - 1].rstrip() + "…"
            rec["why_reason"] = f"close to: {short}"
