from __future__ import annotations

import asyncio
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import (
    CorpusItem,
    GoalsConfig,
    SummarizeRequest,
)
from zotero_summarizer.services.golden import feedback
from zotero_summarizer.services._common import LOGGER, state, unique_non_empty_strings
from zotero_summarizer.storage import repositories as triage_db
from zotero_summarizer.storage.corpus import EmbeddingCache


def empty_corpus_match_result() -> dict[str, Any]:
    return {
        "has_corpus": False,
        "affinity_score": 0.0,
        "positive_similarity": 0.0,
        "negative_similarity": 0.0,
        "matched_goal": "",
        "matched_goal_similarity": 0.0,
        "suggested_collections": [],
        "top_similar_items": [],
    }


def build_corpus_context_text(context: dict[str, Any]) -> str:
    if not context.get("has_corpus"):
        return "No library corpus available yet (it auto-imports from Zotero at startup)."

    similar = context.get("top_similar_items") or []
    similar_text = "\n".join(f"- {item}" for item in similar) if similar else "- none"
    suggested = context.get("suggested_collections") or []
    suggested_text = ", ".join(suggested) if suggested else "none"
    return (
        f"Corpus affinity score: {context.get('affinity_score', 0.0):.3f}\n"
        f"Positive similarity: {context.get('positive_similarity', 0.0):.3f}\n"
        f"Negative similarity: {context.get('negative_similarity', 0.0):.3f}\n"
        f"Matched goal: {context.get('matched_goal', '') or 'none'}"
        f" (similarity={context.get('matched_goal_similarity', 0.0):.3f})\n"
        f"Suggested collections: {suggested_text}\n"
        f"Top similar papers:\n{similar_text}"
    )


def run_corpus_match(req: SummarizeRequest, paper_text: str) -> dict[str, Any]:
    app_state = state()
    config: GoalsConfig = app_state.app_state.config
    if not config.corpus.enabled:
        return empty_corpus_match_result()

    cache: EmbeddingCache | None = getattr(app_state, "embedding_cache", None)
    if cache is None:
        return empty_corpus_match_result()

    abstract_seed = (req.abstract or paper_text[:8000]).strip()
    match = cache.match_candidate(
        title=req.title,
        abstract=abstract_seed,
        doi=req.doi or "",
        stale_days_for_weak_negative=config.corpus.stale_days_for_weak_negative,
    )
    return {
        "has_corpus": match.has_corpus,
        "affinity_score": match.affinity_score,
        "positive_similarity": match.positive_similarity,
        "negative_similarity": match.negative_similarity,
        "matched_goal": match.matched_goal,
        "matched_goal_similarity": match.matched_goal_similarity,
        "suggested_collections": match.suggested_collections,
        "top_similar_items": match.top_similar_items,
    }


def _corpus_item_from_zotero_detail(detail: dict[str, Any]) -> CorpusItem:
    item_id = str(detail.get("item_key") or "").strip()
    title = str(detail.get("title") or "").strip()
    if not item_id or not title:
        raise ValueError("Zotero corpus detail requires an item key and title")

    collections: list[str] = []
    for entry in detail.get("collections") or []:
        if isinstance(entry, dict):
            value = str(entry.get("path") or entry.get("name") or entry.get("key") or "").strip()
        else:
            value = str(entry or "").strip()
        if value:
            collections.append(value)

    return CorpusItem(
        item_id=item_id,
        title=title,
        abstract=str(detail.get("abstract") or "").strip(),
        doi=str(detail.get("doi") or "").strip(),
        tags=unique_non_empty_strings(detail.get("tags") or []),
        collections=unique_non_empty_strings(collections),
        annotation_count=len(detail.get("annotations") or []),
        manual_note_count=len(detail.get("notes") or []),
        created_at=str(detail.get("date_added") or "").strip() or None,
    )


async def auto_import_corpus_from_zotero(page_size: int = 200) -> None:
    """Reconcile listed and cached keys; ``page_size`` bounds detail/write batches."""
    app_state = state()
    reader = getattr(app_state, "zotero_reader", None)
    if reader is None:
        LOGGER.info("Skipping corpus auto-import: Zotero reader unavailable")
        return
    cache = getattr(app_state, "embedding_cache", None)
    if cache is None:
        LOGGER.info("Skipping corpus auto-import: embedding cache unavailable")
        return

    total_imported = 0
    total_updated = 0
    total_feedback = 0
    safe_page_size = max(1, min(int(page_size), 500))

    LOGGER.info("Starting corpus auto-import from Zotero page_size=%s", safe_page_size)
    try:
        page = await asyncio.to_thread(reader.get_all_items, include_abstract=False)
        cached_keys = await asyncio.to_thread(cache.list_item_ids)
        keys = unique_non_empty_strings([row["item_key"] for row in page["items"]] + cached_keys)
        for offset in range(0, len(keys), safe_page_size):
            # ponytail: one detail read per item; batch engagement reads if sync latency warrants it.
            imported, updated, inferred_feedback = await refresh_corpus_items_by_keys(
                keys[offset:offset + safe_page_size]
            )
            total_imported += imported
            total_updated += updated
            total_feedback += inferred_feedback

            LOGGER.info(
                "Corpus auto-import progress processed=%s/%s imported=%s updated=%s feedback=%s",
                min(offset + safe_page_size, len(keys)),
                len(keys),
                total_imported,
                total_updated,
                total_feedback,
            )

        LOGGER.info(
            "Corpus auto-import completed imported=%s updated=%s feedback=%s",
            total_imported,
            total_updated,
            total_feedback,
        )
    except Exception:
        LOGGER.exception("Corpus auto-import failed")
        raise


async def refresh_corpus_items_by_keys(item_keys: list[str]) -> tuple[int, int, int]:
    """Reconcile one batch. Only an explicit missing detail authorizes deletion."""
    normalized_keys = unique_non_empty_strings(item_keys)
    if not normalized_keys:
        return 0, 0, 0

    app_state = state()
    reader = getattr(app_state, "zotero_reader", None)
    if reader is None or getattr(app_state, "embedding_cache", None) is None:
        return 0, 0, 0

    corpus_lock = getattr(app_state, "corpus_write_lock", None)
    if corpus_lock is None:
        corpus_lock = asyncio.Lock()
        app_state.corpus_write_lock = corpus_lock
    async with corpus_lock:
        corpus_items: list[CorpusItem] = []
        missing_keys: list[str] = []
        for item_key in normalized_keys:
            detail = await asyncio.to_thread(reader.get_item_detail, item_key)
            if detail is None:
                missing_keys.append(item_key)
            else:
                item = _corpus_item_from_zotero_detail(detail)
                if item.item_id != item_key:
                    raise ValueError("Zotero detail key does not match the requested corpus key")
                corpus_items.append(item)
        imported, updated = await asyncio.to_thread(
            app_state.embedding_cache.upsert_items, corpus_items, missing_item_ids=missing_keys,
        )

    latest_results = await asyncio.to_thread(triage_db.get_latest_results_for_items, [item.item_id for item in corpus_items])
    events = feedback.infer_feedback_events_from_corpus_items(
        corpus_items, app_state.app_state.config.corpus.stale_days_for_weak_negative, latest_results,
    )
    inferred_feedback = await asyncio.to_thread(triage_db.insert_feedback_events, events) if events else 0
    LOGGER.info(
        "Corpus refreshed for item_keys=%s imported=%s updated=%s feedback=%s missing=%s",
        ",".join(normalized_keys),
        imported,
        updated,
        inferred_feedback,
        len(missing_keys),
    )
    return imported, updated, inferred_feedback


async def corpus_item_metadata(item_key: str) -> dict[str, Any]:
    app_state = state()
    cache: EmbeddingCache | None = getattr(app_state, "embedding_cache", None)
    if cache is None:
        raise APIError(error="corpus_unavailable", message="Corpus cache is not initialized", status_code=503)

    config: GoalsConfig = app_state.app_state.config
    item = await asyncio.to_thread(
        cache.get_item_metadata,
        item_key,
        config.corpus.stale_days_for_weak_negative,
    )
    return {"exists": item is not None, "item": item}


async def corpus_items_metadata(
    limit: int = 200,
    offset: int = 0,
    search: str | None = None,
    sort: str = "updated_at",
    order: str = "desc",
) -> dict[str, Any]:
    app_state = state()
    cache: EmbeddingCache | None = getattr(app_state, "embedding_cache", None)
    if cache is None:
        raise APIError(error="corpus_unavailable", message="Corpus cache is not initialized", status_code=503)

    config: GoalsConfig = app_state.app_state.config
    return await asyncio.to_thread(
        cache.list_items_metadata,
        limit,
        offset,
        search,
        config.corpus.stale_days_for_weak_negative,
        sort,
        order,
    )
