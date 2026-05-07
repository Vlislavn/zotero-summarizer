from __future__ import annotations

import asyncio
import os

from zotero_summarizer.integrations.zotero_read import ZoteroReadError, ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError, ZoteroWriter
from zotero_summarizer.models import AppState
from zotero_summarizer.services import corpus, triage_jobs
from zotero_summarizer.services._adapters import build_llm, build_pdf_extractor
from zotero_summarizer.services._common import LOGGER, read_config, settings, setup_logging, state
from zotero_summarizer.storage import repositories as triage_db
from zotero_summarizer.storage.corpus import EmbeddingCache


def startup() -> None:
    current_settings = settings()
    setup_logging()
    if not current_settings.config_path.exists():
        raise RuntimeError(f"Missing config file: {current_settings.config_path}")

    config = read_config(current_settings.config_path)
    api_key = os.getenv(config.llm.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Environment variable {config.llm.api_key_env} is not set")

    app_state = state()
    app_state.app_state = AppState(config=config)
    app_state.llm_refine = build_llm(config.llm.api_base, config.llm.refine_model, api_key, max_tokens=4096)
    app_state.pdf_extractor = build_pdf_extractor()
    app_state.embedding_cache = EmbeddingCache(current_settings.corpus_db_path, config.corpus.embedding_model)
    if config.corpus.enabled:
        model = app_state.embedding_cache._load_model()
        if model is None:
            LOGGER.warning("Embedding model unavailable at startup; corpus matching will use fallback embeddings")
    app_state.embedding_cache.upsert_goals(config.research_goals)

    triage_db.DB_PATH = current_settings.triage_db_path
    triage_db.init_db()
    app_state.corpus_write_lock = asyncio.Lock()

    app_state.zotero_reader = None
    app_state.zotero_writer = None
    app_state.zotero_error = ""
    try:
        app_state.zotero_reader = ZoteroReader(current_settings.zotero_data_dir)
        app_state.zotero_writer = ZoteroWriter(current_settings.zotero_data_dir)
    except (ZoteroReadError, ZoteroWriteError) as exc:
        app_state.zotero_error = str(exc)
        LOGGER.warning("Zotero local integration disabled: %s", exc)

    app_state.triage_jobs = {}
    interrupted_jobs = triage_db.mark_running_triage_jobs_interrupted()
    persisted_jobs = triage_db.list_triage_jobs(limit=50)
    for job in persisted_jobs:
        app_state.triage_jobs[str(job.get("job_id") or "")] = job
    triage_jobs.trim_job_cache(app_state.triage_jobs)

    resumed_jobs = 0
    auto_corpus_import_started = False
    loop = asyncio.get_event_loop()
    for job in list(app_state.triage_jobs.values()):
        if str(job.get("status") or "") != "interrupted":
            continue
        item_keys = [str(item_key).strip() for item_key in (job.get("item_keys") or []) if str(item_key).strip()]
        completed = int(job.get("completed") or 0)
        if not item_keys or completed >= len(item_keys):
            continue

        job["status"] = "running"
        job["updated_at"] = triage_jobs.now_iso()
        triage_db.upsert_triage_job(job)
        loop.create_task(
            triage_jobs.run_triage_job_worker(
                str(job.get("job_id") or ""),
                item_keys,
                bool(job.get("queue_changes", True)),
            )
        )
        resumed_jobs += 1

    if config.corpus.enabled and app_state.zotero_reader is not None:
        loop.create_task(corpus.auto_import_corpus_from_zotero())
        auto_corpus_import_started = True

    LOGGER.info(
        (
            "Startup complete config=%s timeout=%ss log_file=%s corpus_db=%s embedding_model=%s "
            "zotero_data_dir=%s interrupted_jobs=%s resumed_jobs=%s auto_corpus_import=%s "
            "triage_job_concurrency=%s"
        ),
        current_settings.config_path,
        current_settings.summary_timeout_seconds,
        current_settings.app_log_file,
        current_settings.corpus_db_path,
        config.corpus.embedding_model,
        current_settings.zotero_data_dir,
        interrupted_jobs,
        resumed_jobs,
        auto_corpus_import_started,
        current_settings.triage_job_concurrency,
    )
