# Features

Find a task below, then follow the implementation and test links for details.
Each feature has one paragraph: action, result and the essential boundary;
AI output and passing contract tests are not scientific validation.
Configuration defaults live in the generated [override reference](overrides.md).

## Get started

### Setup and configuration

Run Setup to choose research goals, Zotero paths and AI or ML-only operation; missing project files are created without overwriting existing configuration. Saving settings is separate from verification: local-model checks refuse when effective resources are unknown. [Code](../zotero_summarizer/services/setup/) · [Tests](../tests/test_setup_doctor_runtime_identity.py).

### Model routing

Choose provider profiles and model routes for each AI stage; credentials use the OS keyring or a named environment variable. Unavailable or incompatible execution reports failure rather than a successful answer. [Code](../zotero_summarizer/services/llm/) · [Tests](../tests/test_provider_routing.py).

### Offline models

Prefetch configured ML assets while online to reuse them without downloading again. Strict offline mode refuses uncached acquisition; it does not make hosted inference or new public-source retrieval available offline. [Code](../zotero_summarizer/cli/_app.py) · [Tests](../tests/test_offline.py).

## Find papers

### RSS subscriptions

Add feeds in Settings → Sources, or import existing Zotero subscriptions. Saving a subscription, refreshing entries and triaging papers are separate actions; empty results, offline skips and source failures stay distinguishable. [Code](../zotero_summarizer/services/triage/feeds/) · [Tests](../tests/test_rss_api_routes.py).

### Search

Search by topic and confirmed constraints to get deduplicated candidates and selected reviews. Adding a result to Zotero is explicit; recovery retains the planned item identity rather than silently creating a duplicate after uncertain delivery. [Code](../zotero_summarizer/services/search/) · [Tests](../tests/test_search_materialize_recovery.py).

### Related saved papers

Open Related in my library to find similar saved papers, including already-read ones. Optional note assessment sends complete seed/candidate context to the displayed review route, potentially hosted, only with explicit consent; edits invalidate old private context. [Code](../zotero_summarizer/services/library/) · [Tests](../tests/test_related_client_consent.py).

### Saved-paper discovery

Use Find new relevant on a saved paper to retrieve public OpenAlex candidates while excluding known saved families. Private seed comments require separate consent, stale results are rechecked, and discovery never supplies permission to Add a paper. [Code](../zotero_summarizer/services/search/) · [Tests](../tests/test_discovery_server.py).

## Choose and read

### Daily triage

Today ranks RSS papers against your research goals and lets you Add or Trash them. Feed Add requires a current usable full-text review; Trash does not. The optional daemon reuses this pipeline but never automatically adds a pick to Zotero. [Code](../zotero_summarizer/services/triage/) · [Tests](../tests/test_review_eligibility.py).

### Reading reviews

Open a paper in Library for a digest, reading plan and figures. Claim checks use original sources, not generated summaries; failed attempts preserve a current valid review. Web-article review uses bounded extracted text, not a visual page facsimile. [Code](../zotero_summarizer/services/library/) · [Tests](../tests/test_review_source_boundary.py).

### Paper questions and notes

Ask Paper answers from retained source context with quotes, or abstains when support is unavailable. Questions and manual notes are saved independently of generating an answer; a save acknowledgement is not confirmation of delivery into Zotero. [Code](../zotero_summarizer/services/library/review_questions.py) · [Tests](../tests/test_review_questions.py).

## Save results

### Zotero changes

Explicit Add, metadata edits and approved Pending changes use the backed-up native write path. Pending actions validate the captured selection, and stale rejection refuses the whole selection; an uncertain response does not prove rollback or authorize automatic replay. [Code](../zotero_summarizer/services/zotero/) · [Tests](../tests/test_pending_confirmation.py), [rejection](../tests/test_pending_rejection_atomic.py).

### RSS read-state backups

Read-state sync backs up the database before changing unread markers and rotates backups only after a successful write. Capacity refusal preserves recovery evidence: stop automatic ticks, export and inspect snapshots before making room. A retention failure after commit does not undo the write. [Code](../zotero_summarizer/integrations/) · [Tests](../tests/test_raw_feed_backup.py).

## Learn from decisions

### Labels and learning

Assign, change or retract reading verdicts to supply labels and retain decision history; retraining produces the relevance gate used by the app. Feedback describes observed reviewed items, not unbiased classifier recall. [Code](../zotero_summarizer/services/golden/) · [Tests](../tests/test_verdict_training.py).

### Model history and restore

Use `goldenset model-history` to list snapshots and `goldenset restore-model` to restore one while preserving the current artifact. Restart the daemon to load the restored model; restoring a snapshot does not demonstrate better predictions. [Code](../zotero_summarizer/services/model/classifier_backup.py) · [Tests](../tests/test_model_backup_rollback.py).

## Get digests

### Weekly digests

Open Weekly to browse and download saved research-feed reports with project-oriented engineering cards. Browsing does not generate reviews; CLI generation and optional queued Zotero changes require their own explicit actions. [Code](../zotero_summarizer/services/research_feed/) · [Tests](../tests/test_research_feed_web.py).

### Telegram news

Explicitly authorize a Telegram session and collect admitted posts to produce retained news reports. Authentication, collection and model processing are separate operations; viewing a saved report does not activate an account or collect more messages. [Code](../zotero_summarizer/cli/_telegram.py) · [Tests](../tests/test_telegram_public_cli.py).

### Feedback analysis

Run feedback analysis to inspect retained labels, prediction identities and coverage in local descriptive reports. Weekly scheduling is opt-in; neither manual nor scheduled reports change labels, install a model or establish a causal improvement. [Code](../zotero_summarizer/services/golden/feedback_analysis/) · [Tests](../tests/test_feedback_analysis_operation.py).

## Work across devices and clients

### Offline review

Sync a working set, then read saved context and edit verdicts or notes while disconnected. Device saves remain queued until server acknowledgement; compare both versions before resolving conflicts. PDFs and new AI work remain server-dependent, and browser storage can be lost. [Code](../zotero_summarizer/services/sync/) · [Tests](../tests/test_offline_sync.py).

### Remote access

Use an explicitly configured owner-authenticated HTTPS gateway for remote access; ordinary `serve` remains loopback-only. Gateway/device security needs separate deployment verification, and revocation cannot erase bytes already cached on a device. [Code](../zotero_summarizer/cli/_mobile_serve.py) · [Tests](../tests/test_gateway_real_transport.py).

### MCP tools

Connect an MCP client to the app API to use its operations outside the browser. It shares the app's workflow and acknowledgement boundaries; ingested paper content is not consent to Add, approve or mutate library data. [Code](../zotero_summarizer/mcp/) · [Tests](../tests/test_mcp_search_contract.py).

## Experimental capabilities

### Search question evidence

Opt into original-source question checking to locate quoted answers before review selection. Sufficient checked answers may change recommendation order; partial or unchecked evidence is not a negative answer, and stale evaluator/source provenance cannot remain active. [Code](../zotero_summarizer/services/search/_question_evidence.py) · [Tests](../tests/test_search_question_policy.py).

### Support comparison

Explicitly run the offline support tool to compare decisions over frozen inputs in a private attempt. Admission and inspection are effect-free, ground truth stays outside model inputs, and replay must match its original recording. [Code](../tools/method_validation/) · [Tests](../tests/test_recorded_support_binding.py).

### Priority comparison

Compare admitted native/retained priorities and matched ML/LLM artifacts without changing production ranking. Missing scores and provenance remain unavailable rather than invented or removed from coverage; running requires explicit consent. [Code](../tools/method_validation/PRIORITY-INTERFACES.md) · [Tests](../tests/test_priority_comparison.py).

### Collection comparison

Compare membership choices inside a frozen existing taxonomy, not proposed new collection names. Explicit runs retain their evidence without changing Zotero membership; missing corpus/reference support stays unknown. [Code](../tools/method_validation/COLLECTION-INTERFACES.md) · [Tests](../tests/test_collection_comparison.py).

### Interpretation experiment

Opt into digest/Q&A variants over frozen human-approved sources, with sensitive capture and inspection as separate actions. Original grounding and write authority stay unchanged; a pilot recommendation requires independent human judgments, not merely passing protocol tests. [Code](../zotero_summarizer/cli/_interpretation.py) · [Tests](../tests/test_interpretation_experiment_review_fleet.py).
