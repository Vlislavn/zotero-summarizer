from __future__ import annotations

import asyncio
import os

from zotero_summarizer.integrations.openalex import OpenAlexClient
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
from zotero_summarizer.integrations.unpaywall import UnpaywallClient
from zotero_summarizer.integrations.zotero_read import ZoteroReadError, ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError, ZoteroWriter
from zotero_summarizer.models import AppState
from zotero_summarizer.services import corpus, triage_jobs
from zotero_summarizer.services._adapters import build_llm, build_pdf_extractor
from zotero_summarizer.services._common import LOGGER, read_config, settings, setup_logging, state
from zotero_summarizer.storage import repositories as triage_db
from zotero_summarizer.storage.corpus import EmbeddingCache


def startup(override_model: str | None = None) -> None:
    current_settings = settings()
    setup_logging()
    if not current_settings.config_path.exists():
        raise RuntimeError(f"Missing config file: {current_settings.config_path}")

    config = read_config(current_settings.config_path)
    api_key = os.getenv(config.llm.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Environment variable {config.llm.api_key_env} is not set")

    model_to_use = override_model or config.llm.refine_model
    if override_model:
        LOGGER.info("LLM model override: using %r instead of configured %r", override_model, config.llm.refine_model)

    app_state = state()
    app_state.app_state = AppState(config=config)
    app_state.llm_refine = build_llm(
        config.llm.api_base,
        model_to_use,
        api_key,
        max_tokens=4096,
        extra_body=config.llm.extra_body,
    )
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

    # OpenAlex prestige client (optional — falls back to neutral when disabled).
    # Unpaywall client (optional — Part 4 full-text refine).
    app_state.openalex_client = None
    app_state.unpaywall_client = None
    app_state.openalex_cache = None
    if config.prestige.enabled or config.full_text_refine.enabled:
        ttl_days = max(config.prestige.cache_ttl_days, 1)
        app_state.openalex_cache = OpenAlexCache(
            current_settings.corpus_db_path,
            ttl_seconds=ttl_days * 86400,
        )
    if config.prestige.enabled:
        app_state.openalex_client = OpenAlexClient(
            app_state.openalex_cache, mailto=config.prestige.user_agent_email or None
        )
        LOGGER.info(
            "OpenAlex prestige enabled (weight=%.2f, ttl=%dd, mailto=%s)",
            config.prestige.weight,
            config.prestige.cache_ttl_days,
            "set" if config.prestige.user_agent_email else "unset",
        )
    if config.full_text_refine.enabled:
        if not config.full_text_refine.unpaywall_email:
            LOGGER.warning(
                "full_text_refine enabled but unpaywall_email is empty; "
                "non-arXiv PDFs cannot be resolved without it"
            )
        app_state.unpaywall_client = UnpaywallClient(
            app_state.openalex_cache,
            email=config.full_text_refine.unpaywall_email,
        )
        LOGGER.info(
            "Full-text refine enabled (top_k=%d, max_bytes=%d)",
            config.full_text_refine.top_k,
            config.full_text_refine.max_pdf_bytes,
        )

    # Phase 1.13: hybrid daemon classifier gate. Loads cached model artifact or
    # trains a fresh one based on the current golden CSV's sha256.
    # Per the approved Phase 1.13 plan: missing golden CSV → gate=None (daemon
    # falls back to LLM-on-everything). Any other error from load_or_train
    # (corruption, config typo, training failure) propagates so the user sees
    # it instead of silently running without the gate they enabled.
    app_state.classifier_gate = None
    app_state.classifier_gate_lock = None
    app_state.classifier_gate_training = False
    if config.classifier_gate.enabled:
        from threading import Lock as _Lock
        from zotero_summarizer.services import classifier_persistence

        golden_csv = current_settings.project_root / "zotero-summarizer-golden.csv"
        if not golden_csv.exists():
            LOGGER.warning(
                "classifier_gate.enabled but %s missing; gate disabled "
                "(run `zotero-summarizer goldenset export` first)",
                golden_csv,
            )
        else:
            gate = classifier_persistence.load_or_train(
                golden_csv,
                classifier_name=config.classifier_gate.model_name,
                corpus_db_path=current_settings.corpus_db_path,
                goals_config=config,
                output_dir=classifier_persistence.DEFAULT_MODEL_DIR,
                n_folds=config.classifier_gate.n_folds,
                pca_dim=config.classifier_gate.pca_dim,
            )
            app_state.classifier_gate = gate
            app_state.classifier_gate_lock = _Lock()
            LOGGER.info(
                "Classifier gate ready: %s (n_train=%d, AUC=%.3f, golden_sha=%s, drop=%s)",
                gate.classifier_name,
                gate.training_metadata["n_train"],
                gate.training_metadata["oof_auc"],
                gate.golden_csv_sha256[:12],
                config.classifier_gate.drop_priorities,
            )

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
