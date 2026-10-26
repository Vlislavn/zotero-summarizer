# Features

Find a task below, then follow the implementation and test links for details.
Each feature has one paragraph: action, result and the essential boundary;
AI output and passing contract tests are not scientific validation.
Configuration defaults live in the generated [override reference](overrides.md).

## Get started

### Setup and configuration

Single pending reasons stay visible; multiple unchecked rows fold with their identifiers intact. Typed AI/RSS recovery focuses the existing Settings controls without verification or refresh. Definite rejected config/source/path saves preserve unchanged health; uncertain writes still force a reread, never an automatic retry. Saved ML-only refuses calibration before paper reads or model work; CLI Doctor inventories saved custom hosted routes without full app startup. Run Setup to choose research goals, Zotero paths and AI or ML-only operation; missing project files are created without overwriting existing configuration. Local profile checks follow current model routes even after resuming Setup; existing endpoints remain editable before a nonloading model list. Static-floor eligibility never claims Existing-model capacity. Saving settings is separate from verification: local-model checks refuse when effective resources are unknown, and local mode needs both inference and no-write dry-run success. Status reads derive mode labels from the checklist, not stale saved labels. Optional load-policy files bind approved bytes to provider/model and observed envelope; absent runtime evidence remains unknown, with no automatic approval or hardware certificate. Native request evidence is separate from callback/text success; unresolved ownership survives configuration changes, and exact late completion never resumes a cancelled run. Initial wizard progress reflows at 320/390px while retaining textual current/completion states. [Code](../zotero_summarizer/services/setup/) · [Tests](../tests/test_setup_doctor_runtime_identity.py) · [Browser reflow](../tests/test_setup_surface_browser.py).

### Model routing

Choose provider profiles and model routes for each AI stage; credentials use the OS keyring or a named environment variable. Unavailable or incompatible execution reports failure rather than a successful answer. Resolved-stage snapshots refuse unsupported fields before credential or transport work, without restoring retired requirement APIs; declared capabilities, wire switches and observed operation remain separate. [Code](../zotero_summarizer/services/llm/) · [Tests](../tests/test_provider_routing.py).

### Offline models

Prefetch configured ML assets while online to reuse them without downloading again. Prefetch refuses offline before configuration/acquisition and preserves the flags; `prefetch-models --check` remains nonloading inventory. Strict offline mode refuses uncached acquisition; it does not make hosted inference or new public-source retrieval available offline. [Code](../zotero_summarizer/cli/_app.py) · [Tests](../tests/test_offline.py).

## Find papers

### RSS subscriptions

Add feeds in Settings → Sources, or import existing Zotero subscriptions. Saving a subscription, refreshing entries and triaging papers are separate actions; empty results, offline skips and source failures stay distinguishable. [Code](../zotero_summarizer/services/triage/feeds/) · [Tests](../tests/test_rss_api_routes.py).

### Search

Search by topic and confirmed constraints to get deduplicated candidates and selected reviews. Both Search modes use supported HTTP(S) or canonical identifier links, never title-derived addresses; unsupported inputs remain non-clickable. Adding a result to Zotero is explicit; recovery retains the planned item identity rather than silently creating a duplicate after uncertain delivery. Optional adapter diagnostics distinguish an observed empty result from HTTP, transport or parse failure without retaining response bodies; they do not establish relevance or all-provider coverage. Ordinary public sessions omit an empty Discovery marker without changing saved storage. [Code](../zotero_summarizer/services/search/) · [Tests](../tests/test_search_materialize_recovery.py).

### Related saved papers

Open Related in my library to find similar saved papers, including already-read ones. Optional note assessment sends complete seed/candidate context to the displayed review route, potentially hosted, only with explicit consent; edits invalidate old private context. Generic Search GET and default session serialization exclude stored Related private snapshots/results/history without rewriting them or starting new work; current consent-bound Related actions remain separate. [Code](../zotero_summarizer/services/library/) · [Tests](../tests/test_related_client_consent.py), [session privacy](../tests/test_related_session_projection.py).

### Saved-paper discovery

Use Find new relevant on a saved paper to retrieve public OpenAlex candidates while excluding known saved families. Private seed comments require separate consent, stale results are rechecked, and discovery never supplies permission to Add a paper. [Code](../zotero_summarizer/services/search/) · [Tests](../tests/test_discovery_server.py).

## Choose and read

### Daily triage

Today ranks RSS papers against your research goals and lets you Add or Trash them. Start and watch feed backlog triage in Today; Ops monitors/cancels the separate selected-item jobs started in Library and links to Today rather than starting a backlog drain. Feed Add requires a current usable full-text review; Trash does not. The optional daemon reuses this pipeline but never automatically adds a pick to Zotero. Successful model-refined abstract responses project legacy deep-reading advice to `No` without changing the received refinement, scoring or full-text advice; saved summaries are not retroactively rewritten. [Code](../zotero_summarizer/services/triage/) · [Tests](../tests/test_review_eligibility.py) · [Source-mode boundary](../tests/test_abstract_reading_boundary.py).

### Reading reviews

Open a paper in Library for a digest, reading plan and figures. Unknown job status has an explicit read-only retry, never duplicate generation; number-key verdicts require successful matching paper detail before writing. Stale generated HTML/figures are withheld until rebuilt, and a missing audited figure invalidates completeness without rewriting saved data. The standalone brief keeps one visible ordered plan beside the action, with unknown time distinct from zero and retained capped estimates labeled as saved; withheld actions do not revive stale rationale or targets. Incomplete new reading/writing assessments are refused; legacy display defaults do not replace supplied model fields. Writing-friction reasons have one shared disclosure home rather than a duplicate folded-digest row. Compact rubric labels reflect supplied criteria; failed retrieval remains distinct from an observed goal miss, including under folded remainder headings. Claim checks use original sources, not generated summaries; failed attempts preserve a current valid review. Current cache readers check the retained artifact and bound policy inputs before display projection, without rewriting or regenerating saved data; unsigned consistency is not scientific authentication. Missing verification evidence cannot upgrade a generated digest to current, while saved legacy context remains displayable. Review copies and feed qualification retain raw source records rather than re-signing their presentations. Claim-bearing retained snapshots cannot bypass proof through a no-digest placeholder, and feed consumers require base evidence even without optional project assessment; rejected proof retains observed source availability without rendering its claims. Malformed non-null saved digests remain per-paper errors rather than ordinary pending reviews. Failed duplicate setup also preserves any active review owner rather than allowing a second worker. Web-article review uses bounded extracted text, not a visual page facsimile. [Code](../zotero_summarizer/services/library/) · [Tests](../tests/test_review_source_boundary.py).

### Paper questions and notes

Leaving a paper with an unsaved manual note asks for confirmation across in-app links, programmatic navigation and history. Cancelling keeps the draft; accepting leaves without silently saving it. Clean/saved notes and same-paper anchors do not prompt. Reload/close uses the browser's native unload confirmation. [UI owner](../frontend/src/pages/PaperReviewPage.jsx) · [Navigation regressions](../frontend/src/pages/PaperReviewPage.test.jsx). Question capture does not require a PDF or built review; the same composer stays mounted, while raw-PDF answer modes still require a PDF. Ask Paper answers from retained source context with quotes, or abstains when support is unavailable. Malformed output encoding fails before answer publication, without an extra model repair; Ask HTTP retains the saved-question receipt on this failure. Questions and manual notes are saved independently of generating an answer; eligible question drafts remain mounted through sync/offline transitions, with an explicit retry rather than automatic server replay. A save acknowledgement is not confirmation of delivery into Zotero. [Code](../zotero_summarizer/services/library/review_questions.py) · [Tests](../tests/test_review_questions.py).

## Save results

### Zotero changes

Committed verdicts mirror their current label and owned rationale through the same
system-delivery owner as My notes. Positive applying claims bind source revision,
backup and native target before dispatch; current state is rechecked after claim
commit. Native terminal history precedes explicit deletion ACK. Unknown completion
blocks replay, and these system records never authorize Pending Apply or Reject.
Completed native components are not rewritten on a known partial retry; actual
native changes can still require current-state repair. Read-only reconciliation
without a matching configured writer leaves delivery pending. These local receipts
do not make app and Zotero databases one distributed transaction.
Explicit Add, metadata edits and approved Pending changes use the backed-up native write path. Pending actions validate the captured selection, and stale rejection refuses the whole selection. A received HTTP 409 is shown as stale only when its response body is fully read; if reading fails, Apply remains unconfirmed. An uncertain response does not prove rollback or authorize automatic replay. Verified reviews are published before an exact-revision generated note enters Pending; staging alone is not a saved-to-Zotero acknowledgement, and the review footer does not infer one from retained cache flags. A queue failure preserves the accepted review and reports an error without native retry. [Code](../zotero_summarizer/services/zotero/) · [Tests](../tests/test_pending_confirmation.py), [rejection](../tests/test_pending_rejection_atomic.py).

### RSS read-state backups

Read-state sync backs up the database before changing unread markers and rotates backups only after a successful write. Capacity refusal preserves recovery evidence: stop automatic ticks, export and inspect snapshots before making room. A retention failure after commit does not undo the write. [Code](../zotero_summarizer/integrations/) · [Tests](../tests/test_raw_feed_backup.py).

## Learn from decisions

### Labels and learning

Assign, change or retract reading verdicts to supply labels and retain decision history; retraining produces the relevance gate used by the app. Feedback describes observed reviewed items, not unbiased classifier recall. [Code](../zotero_summarizer/services/golden/) · [Tests](../tests/test_verdict_training.py).

### Model history and restore

Use `goldenset model-history` to list snapshots and `goldenset restore-model` to restore one while preserving the current artifact. Restart the daemon to load the restored model; restoring a snapshot does not demonstrate better predictions. [Code](../zotero_summarizer/services/model/classifier_backup.py) · [Tests](../tests/test_model_backup_rollback.py).

## Get digests

### Weekly digests

Open Weekly to browse and download saved research-feed reports with project-oriented engineering cards. Browsing does not generate reviews; CLI generation and optional queued Zotero changes require their own explicit actions. Generation keeps nullable history objects unobserved rather than rejecting healthy papers; malformed non-null fields and blank/non-string payloads refuse. Consumed-number diagnostics are bounded without echoing bad scalar inputs. Native admission remains separate from descriptive projection; prebudget history/disposition provenance retains each source once across output homes and cuts, without inventing prediction for a missing qualified cache. Missing/unqualified reviews remain pending, not evidence that full text is unavailable; only observed acquisition failure enters the manual-text list. Pending reviews stay visible with separate reasons and no-auto-run paper links. Present malformed pending rows refuse display/download; legacy field absence and valid saved values stay unchanged. Bound source availability survives generation or card-projection failure, without automatic retry or backfill. Optional proposals must match the qualified base audit's original text and extent; an intact proposal checksum alone cannot authorize them. Markdown presents reproduction fields once, retaining explicit unrecorded states without migrating saved reports. New report provenance exports source hashes and a retained-receipt reference, not private source paths or focus input; original review receipts and prior report bytes stay intact. Cached abstract summaries refuse coercible booleans/numeric strings even with a matching checksum, without implicit regeneration. Exact configured-project goal observations remain explicit unverified notes, without becoming project-use assessments or new ideas; raw text/order and prior bundles are preserved. Pre-existing output-root/state links refuse before Weekly work; the shared publisher also refuses linked roots before staging, without claiming filesystem-race containment. [Code](../zotero_summarizer/services/research_feed/) · [Tests](../tests/test_research_feed_web.py) · [History admission](../tests/test_research_feed_ranking.py).

### Telegram news

Explicitly authorize a Telegram session and collect admitted posts to produce retained news reports. Authentication, collection and model processing are separate operations; viewing a saved report does not activate an account or collect more messages. Saved processing refuses aliased known output parent/directory/state owners before inputs or models and rechecks before publication, without claiming filesystem-race containment. [Code](../zotero_summarizer/cli/_telegram.py) · [Tests](../tests/test_telegram_public_cli.py).

### Feedback analysis

Run feedback analysis to inspect retained labels, prediction identities and coverage in local descriptive reports. Weekly scheduling is opt-in; neither manual nor scheduled reports change labels, install a model or establish a causal improvement. [Code](../zotero_summarizer/services/golden/feedback_analysis/) · [Tests](../tests/test_feedback_analysis_operation.py).

## Work across devices and clients

### Offline review

Sync a working set, then read saved context and edit verdicts or notes while disconnected. Device saves remain queued until server acknowledgement; compare both versions before resolving conflicts. A known transport outage permits a cached detail refresh even if the browser reports online; observed server refusals are still errors. PDFs and new AI work remain server-dependent, and browser storage can be lost. [Code](../zotero_summarizer/services/sync/) · [Tests](../tests/test_offline_sync.py).

### Remote access

Use an explicitly configured owner-authenticated HTTPS gateway for remote access; ordinary `serve` remains loopback-only. Gateway/device security needs separate deployment verification, and revocation cannot erase bytes already cached on a device. [Code](../zotero_summarizer/cli/_mobile_serve.py) · [Tests](../tests/test_gateway_real_transport.py).

### MCP tools

Connect an MCP client to the app API to use its operations outside the browser. Similarity requires a nonblank query/seed title and actual semantic confirmation; ordinary queue fallback is not a recommendation. It shares the app's workflow and acknowledgement boundaries; ingested paper content is not consent to Add, approve or mutate library data. [Code](../zotero_summarizer/mcp/) · [Tests](../tests/test_mcp_search_contract.py).

## Experimental capabilities

### Search question evidence

Opt into original-source question checking to locate quoted answers before review selection. Sufficient checked answers may change recommendation order; partial or unchecked evidence is not a negative answer, and stale evaluator/source provenance cannot remain active. [Code](../zotero_summarizer/services/search/_question_evidence.py) · [Tests](../tests/test_search_question_policy.py).

### Support comparison

Explicitly run the offline support tool to compare decisions over frozen inputs in a private attempt. Admission and inspection are effect-free, ground truth stays outside model inputs, and replay must match its original recording plus admitted native dispatch/byte observations, reservation count and file roles; self-consistent output hashes alone confer no authority. Direct transport argument errors refuse before call accounting or HTTP; valid finite overrides retain configured caps. All three task consumers preserve remaining total run time separately from idle gaps at stream checkpoints, without a remote-termination guarantee. [Code](../tools/method_validation/) · [Tests](../tests/test_recorded_support_binding.py).

### Priority comparison

Compare admitted native/retained priorities and matched ML/LLM artifacts without changing production ranking. Missing scores and provenance remain unavailable rather than invented or removed from coverage; running requires explicit consent. Omitted or null evidence stays per-paper unknown without native dispatch; operational failures cannot retain a semantic readout. Replay checks native dispatch/byte consistency and admitted publication roles, without authenticating server measurements. [Code](../tools/method_validation/PRIORITY-INTERFACES.md) · [Tests](../tests/test_priority_comparison.py).

### Collection comparison

Compare membership choices inside a frozen existing taxonomy, not proposed new collection names. Explicit runs retain their evidence without changing Zotero membership; missing corpus/reference support stays unknown. Native replay shares the adapter's transport consistency check, rejecting boolean aliases for integer attempt/retry counters. Optional declared version/derivation graphs retain transitive holdout exclusions in replayed audits and refuse contaminated active references; unsigned or absent lineage is not authenticated purge proof. Shortlists require frozen provenance, and native no-match requires every distinct eligible question. [Code](../tools/method_validation/COLLECTION-INTERFACES.md) · [Tests](../tests/test_collection_comparison.py).

### Interpretation experiment

Opt into digest/Q&A variants over frozen human-approved sources, with sensitive capture and inspection as separate actions. The optional note card shares Library's qualified Finding/Scope/Unproven projection, including complete fields/bases and one ordered support disclosure; omitted/disabled cards preserve ordinary note HTML. Ordinary readers/export refuse reserved experimental payloads, and retained item normalization cannot conceal changed evidence. Saved records decode verified bytes, while optional-only schema/source drift withholds the card without discarding valid base evidence. Original grounding and write authority stay unchanged; a pilot recommendation requires independent human judgments, not merely passing protocol tests. [Code](../zotero_summarizer/cli/_interpretation.py) · [Tests](../tests/test_interpretation_experiment_review_fleet.py).
