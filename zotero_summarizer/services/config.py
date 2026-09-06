from __future__ import annotations

import asyncio

from zotero_summarizer.models import AppState, GoalsConfig
from zotero_summarizer.services import corpus
from zotero_summarizer.services._common import LOGGER, settings, state, write_user_config
from zotero_summarizer.services.config_overrides import apply_calibration, apply_env_overrides
from zotero_summarizer.storage.corpus import EmbeddingCache


async def get_runtime_config() -> dict:
    config: GoalsConfig = state().app_state.config
    # JSON mode: coerces enums (e.g. ProviderType) to their string values so the
    # shape matches what PUT persists and what the frontend round-trips back.
    return config.model_dump(mode="json")


async def update_runtime_config(new_config: GoalsConfig) -> dict:
    """Persist + hot-swap the config. LLM clients are NOT rebuilt here: each
    stage rebuilds lazily on next use (``invalidate_stage_clients`` clears the
    cache). Provider availability is no longer validated on save — the app must
    accept config for an endpoint that is currently down (run the manual check
    via ``POST /api/admin/llm-check`` to verify). Only the embedding cache, which
    is local and load-bearing for corpus matching, is rebuilt eagerly.
    """
    current_settings = settings()
    # Re-apply per-user calibration THEN ``ZS_*`` env so both survive a UI save (the
    # request body carries only user-owned keys; calibration + env stay authoritative
    # over system-owned defaults after the hot-swap, env winning). config_overrides.py.
    new_config = apply_calibration(new_config, current_settings.calibration_path)
    new_config = apply_env_overrides(new_config)
    # JSON mode coerces ProviderType enums to their .value. This payload is the
    # full EFFECTIVE config returned to the caller; the DISK write below persists
    # only the user-owned keys (write_user_config).
    payload = new_config.model_dump(mode="json")

    app_state = state()
    existing_cache: EmbeddingCache | None = getattr(app_state, "embedding_cache", None)
    previous_model_name = existing_cache.model_name if existing_cache is not None else None
    model_changed = previous_model_name is not None and previous_model_name != new_config.corpus.embedding_model
    # Captured BEFORE the swap: edited research goals invalidate every persisted
    # per-item goal_sim (the slate's rank-blend input), so a background slate
    # rescore is scheduled after the save (see below).
    goals_changed = list(app_state.app_state.config.research_goals) != list(new_config.research_goals)

    def prepare_embedding_cache() -> EmbeddingCache:
        cache = EmbeddingCache(current_settings.corpus_db_path, new_config.corpus.embedding_model)
        if new_config.corpus.enabled:
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

        write_user_config(current_settings.config_path, new_config)
        app_state.app_state = AppState(config=new_config)
        app_state.invalidate_stage_clients()
        app_state.embedding_cache = new_embedding_cache

    if model_changed and new_config.corpus.enabled and getattr(app_state, "zotero_reader", None) is not None:
        asyncio.create_task(corpus.auto_import_corpus_from_zotero())

    if goals_changed:
        # Persisted per-item goal_sims were computed against the OLD goals; the
        # Today slate would silently rank on stale signals until the next
        # retrain/startup rescore. Background, never blocks the save. Lazy
        # import mirrors _gate._rescore_slate_after_swap (module-cycle guard).
        from zotero_summarizer.services.triage.feeds import schedule_slate_rescore_async

        schedule_slate_rescore_async("goals-updated")

    return {"status": "ok", "config": payload}
