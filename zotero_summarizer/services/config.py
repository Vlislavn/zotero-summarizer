from __future__ import annotations

import asyncio
import os

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import AppState, GoalsConfig
from zotero_summarizer.services import corpus
from zotero_summarizer.services._adapters import build_llm
from zotero_summarizer.services._common import LOGGER, settings, state, write_config_atomic
from zotero_summarizer.storage.corpus import EmbeddingCache


async def get_runtime_config() -> dict:
    config: GoalsConfig = state().app_state.config
    return config.model_dump(mode="python")


async def update_runtime_config(new_config: GoalsConfig) -> dict:
    current_settings = settings()
    payload = new_config.model_dump(mode="python")
    api_key = os.getenv(new_config.llm.api_key_env, "")
    if not api_key:
        raise APIError(
            error="missing_api_key",
            message=f"Environment variable {new_config.llm.api_key_env} is not set",
            status_code=400,
        )

    new_llm_refine = await asyncio.to_thread(
        lambda: build_llm(
            new_config.llm.api_base,
            new_config.llm.refine_model,
            api_key,
            4096,
            extra_body=new_config.llm.extra_body,
        )
    )

    app_state = state()
    existing_cache: EmbeddingCache | None = getattr(app_state, "embedding_cache", None)
    previous_model_name = existing_cache.model_name if existing_cache is not None else None
    model_changed = previous_model_name is not None and previous_model_name != new_config.corpus.embedding_model

    def prepare_embedding_cache() -> EmbeddingCache:
        cache = EmbeddingCache(current_settings.corpus_db_path, new_config.corpus.embedding_model)
        cache.upsert_goals(new_config.research_goals)
        return cache

    new_embedding_cache = await asyncio.to_thread(prepare_embedding_cache)
    corpus_lock: asyncio.Lock | None = getattr(app_state, "corpus_write_lock", None)
    if corpus_lock is None:
        corpus_lock = asyncio.Lock()
        app_state.corpus_write_lock = corpus_lock

    async with corpus_lock:
        if model_changed:
            cleared = await asyncio.to_thread(new_embedding_cache.clear_corpus_embeddings)
            LOGGER.info("Embedding model changed; cleared %s corpus embeddings", cleared)

        write_config_atomic(current_settings.config_path, payload)
        app_state.app_state = AppState(config=new_config)
        app_state.llm_refine = new_llm_refine
        app_state.embedding_cache = new_embedding_cache

    if model_changed and new_config.corpus.enabled and getattr(app_state, "zotero_reader", None) is not None:
        asyncio.create_task(corpus.auto_import_corpus_from_zotero())

    return {"status": "ok", "config": payload}
