from __future__ import annotations

import asyncio
import os

from zotero_summarizer.integrations.openalex import OpenAlexClient
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
from zotero_summarizer.integrations.unpaywall import UnpaywallClient
from zotero_summarizer.integrations.zotero_read import ZoteroReadError, ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError, ZoteroWriter
from zotero_summarizer.models import AppState, GoalsConfig
from zotero_summarizer.runtime import RuntimeState
from zotero_summarizer.services import corpus
from zotero_summarizer.services.triage import triage_jobs
from zotero_summarizer.services._adapters import build_pdf_extractor
from zotero_summarizer.services._common import LOGGER, read_config, settings, setup_logging, state
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories as triage_db
from zotero_summarizer.storage.corpus import EmbeddingCache


def _load_config(current_settings: Settings, override_model: str | None) -> GoalsConfig:
    """Read + validate goals.yaml.

    The app starts even when LLM providers are unreachable or their API keys are
    unset — provider validation is decoupled from startup (run the manual check
    via ``POST /api/admin/llm-check``). A missing/invalid config *file* is still
    fatal: that's a different failure class from a down endpoint.

    ``override_model`` (CLI ``--model``) overrides the default stage model that
    every stage inherits unless it sets its own.
    """
    if not current_settings.config_path.exists():
        raise RuntimeError(f"Missing config file: {current_settings.config_path}")

    config = read_config(current_settings.config_path)
    if override_model:
        LOGGER.info(
            "LLM model override: default stage model %r -> %r",
            config.llm_routing.default.model, override_model,
        )
        config.llm_routing.default.model = override_model
    return config


def _init_models(
    app_state: RuntimeState,
    config: GoalsConfig,
    current_settings: Settings,
) -> None:
    """Wire the PDF extractor + embedding cache singletons.

    LLM clients are NOT built here: each pipeline stage resolves its own client
    lazily on first use via ``RuntimeState.resolve_stage_client`` (so a missing
    key/unreachable endpoint never blocks startup)."""
    app_state.app_state = AppState(config=config)
    app_state.invalidate_stage_clients()
    app_state.pdf_extractor = build_pdf_extractor()
    app_state.embedding_cache = EmbeddingCache(current_settings.corpus_db_path, config.corpus.embedding_model)
    if config.corpus.enabled:
        model = app_state.embedding_cache._load_model()
        if model is None:
            LOGGER.warning("Embedding model unavailable at startup; corpus matching will use fallback embeddings")
    app_state.embedding_cache.upsert_goals(config.research_goals)


def _init_database(current_settings: Settings, app_state: RuntimeState) -> None:
    triage_db.DB_PATH = current_settings.triage_db_path
    triage_db.init_db()
    app_state.corpus_write_lock = asyncio.Lock()


def _init_metadata_clients(app_state: RuntimeState, config: GoalsConfig, current_settings: Settings) -> None:
    """OpenAlex prestige + Unpaywall full-text clients (both optional)."""
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


def _init_classifier_gate(app_state: RuntimeState, config: GoalsConfig, current_settings: Settings) -> None:
    """Phase 1.13: hybrid daemon classifier gate. Startup must stay fast, so it
    NEVER retrains synchronously: it loads the cached artifact as-is (even if
    its golden sha is stale after a Refresh-labels export) and delegates any
    needed (re)train to a background thread. This fixes the >8 min hang where
    a golden-CSV sha drift forced a synchronous retrain under CPU contention.
    Per the approved Phase 1.13 plan: missing golden CSV → gate=None (daemon
    falls back to LLM-on-everything). A genuine read error on the cached
    artifact propagates so the user sees corruption instead of running blind.
    """
    app_state.classifier_gate = None
    app_state.classifier_gate_lock = None
    app_state.classifier_gate_training = False
    app_state.classifier_gate_error = ""
    if not config.classifier_gate.enabled:
        return

    from threading import Lock as _Lock
    from zotero_summarizer.services.model import classifier, classifier_persistence
    from zotero_summarizer.services.triage import feeds

    golden_csv = current_settings.golden_csv_path
    if not golden_csv.exists():
        LOGGER.warning(
            "classifier_gate.enabled but %s missing; gate disabled "
            "(run `zotero-summarizer goldenset export` first)",
            golden_csv,
        )
        return

    # Lock must be set before scheduling the background retrain so the worker
    # can atomically swap the gate in.
    app_state.classifier_gate_lock = _Lock()
    model_path = (
        classifier_persistence.DEFAULT_MODEL_DIR
        / f"{config.classifier_gate.model_name}.joblib"
    )
    gate = None
    if model_path.exists():
        try:
            gate = classifier_persistence.load_trained(model_path)
        except (ModuleNotFoundError, ImportError, AttributeError) as exc:
            # The artifact was pickled against an older module layout (e.g.
            # before the services/ domain reorg moved classifier_persistence
            # under services/model/). It can't be unpickled now. Treat it like a
            # stale model: leave the gate off and retrain in the background,
            # which re-saves the artifact under the current import paths
            # (self-healing). A regenerable cache file must never brick startup.
            LOGGER.warning(
                "Cached classifier %s is incompatible with the current "
                "module layout (%s); ignoring it and retraining in "
                "background (gate off until ready)",
                config.classifier_gate.model_name, exc,
            )
    # A cached model trained against an older feature pipeline (e.g. a Sprint-1
    # 777-dim artifact when the builder now emits 780) can't predict the current
    # feature matrix and would crash every triage. Treat it like "no usable
    # model": leave the gate off and retrain in the background, rather than
    # serving a model that throws.
    if gate is not None and gate.feature_dim != classifier.FEATURE_DIM:
        LOGGER.warning(
            "Cached classifier %s has feature_dim=%d but the builder now "
            "emits %d; ignoring the stale model and retraining in "
            "background (gate off until ready)",
            config.classifier_gate.model_name,
            gate.feature_dim,
            classifier.FEATURE_DIM,
        )
        gate = None
    if gate is not None:
        app_state.classifier_gate = gate
        # Sprint-1 swapped the gate's objective to regression (oof_spearman).
        # Older runs stored oof_auc. Surface whichever the current model carries
        # so startup never breaks on a missing key.
        md = gate.training_metadata
        quality_label = (
            f"AUC={md['oof_auc']:.3f}" if "oof_auc" in md else
            f"Spearman={md['oof_spearman']:.3f}" if "oof_spearman" in md else
            "quality=n/a"
        )
        LOGGER.info(
            "Classifier gate loaded (cached): %s (n_train=%d, %s, golden_sha=%s, drop=%s)",
            gate.classifier_name,
            md["n_train"],
            quality_label,
            gate.golden_csv_sha256[:12],
            config.classifier_gate.drop_priorities,
        )
        # No-op if the golden sha is unchanged; background retrain if it drifted
        # (e.g. after Refresh-labels re-exported the CSV). A drift retrain swaps
        # AND re-scores the slate via install_gate; when nothing drifts, the
        # loaded cached gate is final, so re-score the slate ourselves — a model
        # trained offline (CLI) then loaded on this restart would otherwise leave
        # Today on whatever scores each row got at triage time.
        if not feeds.schedule_gate_retrain_async("startup"):
            feeds.schedule_slate_rescore_async("startup-cached-gate")
    else:
        # No usable cached model (missing, or rejected as stale above).
        LOGGER.info(
            "Classifier gate %s not ready; training in background "
            "(gate off until ready, daemon LLM-scores everything meanwhile)",
            config.classifier_gate.model_name,
        )
        feeds.schedule_gate_retrain_async("startup", allow_initial=True)


def _init_zotero(app_state: RuntimeState, current_settings: Settings) -> None:
    app_state.zotero_reader = None
    app_state.zotero_writer = None
    app_state.zotero_error = ""
    try:
        app_state.zotero_reader = ZoteroReader(current_settings.zotero_data_dir)
        app_state.zotero_writer = ZoteroWriter(current_settings.zotero_data_dir)
    except (ZoteroReadError, ZoteroWriteError) as exc:
        app_state.zotero_error = str(exc)
        LOGGER.warning("Zotero local integration disabled: %s", exc)


def _load_persisted_jobs(app_state: RuntimeState) -> int:
    """Reload persisted triage jobs into the in-memory cache; return the number
    marked interrupted (running jobs from a previous process that died)."""
    app_state.triage_jobs = {}
    interrupted_jobs = triage_db.mark_running_triage_jobs_interrupted()
    persisted_jobs = triage_db.list_triage_jobs(limit=50)
    for job in persisted_jobs:
        app_state.triage_jobs[str(job.get("job_id") or "")] = job
    triage_jobs.trim_job_cache(app_state.triage_jobs)
    return interrupted_jobs


def _resume_interrupted_jobs(app_state: RuntimeState, loop: asyncio.AbstractEventLoop | None) -> int:
    """Re-schedule interrupted jobs that still have unprocessed items. Without a
    running loop (test mode) jobs are flagged running but not scheduled."""
    resumed_jobs = 0
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
        if loop is not None:
            loop.create_task(
                triage_jobs.run_triage_job_worker(
                    str(job.get("job_id") or ""),
                    item_keys,
                    bool(job.get("queue_changes", True)),
                )
            )
            resumed_jobs += 1
    return resumed_jobs


def startup(override_model: str | None = None) -> None:
    current_settings = settings()
    setup_logging()

    if os.getenv("HF_HUB_OFFLINE") == "1":
        LOGGER.info("Offline mode: HuggingFace hub disabled — models load cache-only "
                    "(run `zotero-summarizer prefetch-models` online once to populate the cache)")

    config = _load_config(current_settings, override_model)
    app_state = state()

    _init_models(app_state, config, current_settings)
    _init_database(current_settings, app_state)
    _init_metadata_clients(app_state, config, current_settings)
    _init_classifier_gate(app_state, config, current_settings)
    _init_zotero(app_state, current_settings)

    # Loud boot-time readiness sweep so a missing critical dep (e.g. lightgbm)
    # is visible in the log at once, not discovered later as a silent gate=None.
    # The gate itself trains async — a "not ready yet" here is informational.
    from zotero_summarizer.services import readiness
    for st in readiness.all_statuses():
        if st.ready:
            continue
        if st.name == "classifier_gate":
            LOGGER.info("readiness: %s not ready yet — %s", st.name, st.detail)
        else:
            LOGGER.error("readiness: %s NOT READY — %s", st.name, st.detail)

    interrupted_jobs = _load_persisted_jobs(app_state)

    # ``get_running_loop`` raises if there's no running loop (test mode);
    # ``get_event_loop`` is deprecated and creates a phantom loop whose tasks
    # never run, producing "coroutine never awaited" warnings. We take the
    # running loop when available and otherwise skip task scheduling —
    # production (FastAPI lifespan) always has a loop.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    resumed_jobs = _resume_interrupted_jobs(app_state, loop)

    auto_corpus_import_started = False
    if config.corpus.enabled and app_state.zotero_reader is not None and loop is not None:
        loop.create_task(corpus.auto_import_corpus_from_zotero())
        auto_corpus_import_started = True

    # Launch-time deep-review prewarm: background-compute the top-K not-yet-cached
    # reviews so the first open is instant (gated by quality_review.prewarm_on_startup_k
    # / ZS_DEEP_REVIEW_PREWARM_K). Local import mirrors _init_classifier_gate's lazy
    # imports — keeps the startup module from depending on the library layer at load.
    from zotero_summarizer.services.library import deep_review_prewarm
    prewarm_scheduled = deep_review_prewarm.schedule_on_startup(config, app_state)

    # Launch-time review-fleet prewarm: pre-decide a reading verdict for the same
    # top-K picks (Confirm/Override instead of decide-from-scratch). Reuses the
    # cached deep reviews the prewarm above warms, so it adds no extra model load.
    from zotero_summarizer.services.library.review_fleet import prewarm as review_fleet_prewarm
    review_fleet_scheduled = review_fleet_prewarm.schedule_on_startup(config, app_state)

    LOGGER.info(
        (
            "Startup complete config=%s timeout=%ss log_file=%s corpus_db=%s embedding_model=%s "
            "zotero_data_dir=%s interrupted_jobs=%s resumed_jobs=%s auto_corpus_import=%s "
            "deep_review_prewarm=%s review_fleet_prewarm=%s triage_job_concurrency=%s"
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
        prewarm_scheduled,
        review_fleet_scheduled,
        current_settings.triage_job_concurrency,
    )
