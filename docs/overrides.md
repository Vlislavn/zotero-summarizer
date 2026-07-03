# Environment overrides (`ZS_*`)

Operator escape hatch for the **system-owned** config knobs. A human edits only
intent (`goals.yaml`); these override a validated code default without touching code.

Precedence: **code default < `goals.yaml` < `data/calibration.json` < `ZS_*` env**.

> Generated from `zotero_summarizer/services/config_overrides.py` — do not edit by hand.
> A test asserts this file matches the registry.

| Env var | Config path | Type | Default |
|---|---|---|---|
| `ZS_CORPUS_ENABLED` | `corpus.enabled` | bool | true |
| `ZS_CORPUS_EMBEDDING_MODEL` | `corpus.embedding_model` | str | sentence-transformers/all-MiniLM-L6-v2 |
| `ZS_CORPUS_SIMILARITY_THRESHOLD` | `corpus.similarity_threshold` | float | -0.3 |
| `ZS_CORPUS_STALE_DAYS_FOR_WEAK_NEGATIVE` | `corpus.stale_days_for_weak_negative` | int | 30 |
| `ZS_CORPUS_BM25_ENABLED` | `corpus.bm25_enabled` | bool | true |
| `ZS_CORPUS_RERANKER_ENABLED` | `corpus.reranker_enabled` | bool | true |
| `ZS_CORPUS_RERANKER_MODEL` | `corpus.reranker_model` | str | BAAI/bge-reranker-v2-m3 |
| `ZS_PRESTIGE_ENABLED` | `prestige.enabled` | bool | true |
| `ZS_PRESTIGE_WEIGHT` | `prestige.weight` | float | 0.15 |
| `ZS_PRESTIGE_CACHE_TTL_DAYS` | `prestige.cache_ttl_days` | int | 30 |
| `ZS_PRESTIGE_FALLBACK_NEUTRAL` | `prestige.fallback_neutral` | float | 3.0 |
| `ZS_PRESTIGE_USER_AGENT_EMAIL` | `prestige.user_agent_email` | str | (empty) |
| `ZS_PRESTIGE_REQUIRE_DOI` | `prestige.require_doi` | bool | false |
| `ZS_PRESTIGE_COLD_START_AUTHOR_LIFT` | `prestige.cold_start_author_lift` | bool | true |
| `ZS_PRESTIGE_COLD_START_MAX_LIFT` | `prestige.cold_start_max_lift` | float | 1.0 |
| `ZS_PRESTIGE_COLD_START_GAMMA` | `prestige.cold_start_gamma` | float | 1.5 |
| `ZS_FULL_TEXT_REFINE_ENABLED` | `full_text_refine.enabled` | bool | false |
| `ZS_FULL_TEXT_REFINE_TOP_K` | `full_text_refine.top_k` | int | 2 |
| `ZS_FULL_TEXT_REFINE_MAX_PDF_BYTES` | `full_text_refine.max_pdf_bytes` | int | 50000000 |
| `ZS_FULL_TEXT_REFINE_FETCH_TIMEOUT_SECS` | `full_text_refine.fetch_timeout_secs` | float | 30.0 |
| `ZS_FULL_TEXT_REFINE_UNPAYWALL_EMAIL` | `full_text_refine.unpaywall_email` | str | (empty) |
| `ZS_RECOVER_ABSTRACT_ENABLED` | `recover_abstract.enabled` | bool | true |
| `ZS_RECOVER_ABSTRACT_GOAL_SIM_THRESHOLD` | `recover_abstract.goal_sim_threshold` | float | 0.45 |
| `ZS_RECOVER_ABSTRACT_MAX_PER_TICK` | `recover_abstract.max_per_tick` | int | 3 |
| `ZS_RECOVER_ABSTRACT_MIN_ABSTRACT_CHARS` | `recover_abstract.min_abstract_chars` | int | 120 |
| `ZS_QUALITY_REVIEW_ENABLED` | `quality_review.enabled` | bool | true |
| `ZS_QUALITY_REVIEW_TOP_K` | `quality_review.top_k` | int | 5 |
| `ZS_DEEP_REVIEW_PREWARM_K` | `quality_review.prewarm_on_startup_k` | int | 5 |
| `ZS_QUALITY_REVIEW_AUTO_ON_TICK_K` | `quality_review.auto_on_tick_k` | int | 10 |
| `ZS_QUALITY_REVIEW_RENDER_ON_TICK_K` | `quality_review.render_on_tick_k` | int | 3 |
| `ZS_QUALITY_BAND_PRIMARY` | `quality_review.quality_band_primary` | bool | false |
| `ZS_QUALITY_REVIEW_QUALITY_PROMOTE` | `quality_review.quality_promote` | bool | true |
| `ZS_QUALITY_REVIEW_QUALITY_PROMOTE_GOAL_SIM` | `quality_review.quality_promote_goal_sim` | float | 0.55 |
| `ZS_QUALITY_REVIEW_QUALITY_PROMOTE_RELEVANCE_FLOOR` | `quality_review.quality_promote_relevance_floor` | float | 3.0 |
| `ZS_QUALITY_REVIEW_MAX_PDF_BYTES` | `quality_review.max_pdf_bytes` | int | 50000000 |
| `ZS_QUALITY_REVIEW_FETCH_TIMEOUT_SECS` | `quality_review.fetch_timeout_secs` | float | 30.0 |
| `ZS_QUALITY_REVIEW_MAX_TEXT_CHARS` | `quality_review.max_text_chars` | int | 60000 |
| `ZS_QUALITY_REVIEW_SELF_CONSISTENCY_RUNS` | `quality_review.self_consistency_runs` | int | 3 |
| `ZS_QUALITY_REVIEW_LEAN_SELF_CONSISTENCY_RUNS` | `quality_review.lean_self_consistency_runs` | int | 1 |
| `ZS_QUALITY_REVIEW_LEAN_MAX_TEXT_CHARS` | `quality_review.lean_max_text_chars` | int | 12000 |
| `ZS_QUALITY_REVIEW_BATCH_GOAL_SUMMARIES` | `quality_review.batch_goal_summaries` | bool | true |
| `ZS_QUALITY_REVIEW_UNPAYWALL_EMAIL` | `quality_review.unpaywall_email` | str | (empty) |
| `ZS_QUALITY_REVIEW_SHADOW_CLAIM_CHECK` | `quality_review.shadow_claim_check` | bool | false |
| `ZS_QUALITY_REVIEW_CLAIM_CHECK_MODEL` | `quality_review.claim_check_model` | str | flan-t5-large |
| `ZS_QUALITY_REVIEW_SELF_VERIFICATION` | `quality_review.self_verification` | bool | true |
| `ZS_QUALITY_REVIEW_USE_DOCLING` | `quality_review.use_docling` | bool | false |
| `ZS_QUALITY_REVIEW_REVIEW_WEB_ARTICLES` | `quality_review.review_web_articles` | bool | true |
| `ZS_QUALITY_REVIEW_CHUNK_STRATEGY` | `quality_review.chunk_strategy` | str | rank |
| `ZS_QUALITY_REVIEW_MAP_CHUNK_CHARS` | `quality_review.map_chunk_chars` | int | 8000 |
| `ZS_AUTO_QUALITY_GATE` | `quality_review.auto_quality_gate` | bool | true |
| `ZS_AUTO_QUALITY_LLM_FLOOR` | `quality_review.auto_quality_llm_floor` | int | 2 |
| `ZS_AUTO_QUALITY_HIDE_GRADES` | `quality_review.auto_quality_hide_grades` | list[str] | ('D',) |
| `ZS_AUTO_QUALITY_HIDE_BANDS` | `quality_review.auto_quality_hide_bands` | list[str] | ('flag',) |
| `ZS_CLASSIFIER_GATE_ENABLED` | `classifier_gate.enabled` | bool | true |
| `ZS_CLASSIFIER_GATE_MODEL_NAME` | `classifier_gate.model_name` | str | lightgbm |
| `ZS_CLASSIFIER_GATE_DROP_PRIORITIES` | `classifier_gate.drop_priorities` | list[str] | dont_read |
| `ZS_CLASSIFIER_GATE_BULK_DRAIN_GATE_ONLY` | `classifier_gate.bulk_drain_gate_only` | bool | true |
| `ZS_CLASSIFIER_GATE_PCA_DIM` | `classifier_gate.pca_dim` | int | 100 |
| `ZS_CLASSIFIER_GATE_N_FOLDS` | `classifier_gate.n_folds` | int | 5 |
| `ZS_CLASSIFIER_GATE_RAW_SCORE_DONT_READ_BELOW` | `classifier_gate.raw_score_dont_read_below` | float | 0.0 |
| `ZS_CLASSIFIER_GATE_AUDIT_SAMPLE_PER_TICK` | `classifier_gate.audit_sample_per_tick` | int | 1 |
