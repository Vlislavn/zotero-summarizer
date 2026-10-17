# models — Pydantic contract (config + API shapes)

`CompositeNoteIntent`, `UserNotePayload` and `MirrorNoteRequest` close system-mirror
intent/target/backup data without Pending authority. Full body/revision/resolved key
bind delivery; formatting stays service-owned. Source candidate, not runtime or
whole #7 acceptance.

Request/response, configuration and domain data contracts, split by group.
The package root re-exports each module's declared `__all__`; other types and
helpers require direct module imports.

```
config.py ──GoalsConfig──> api.py (AppState)
triage.py ──(domain)        all three ──*──> __init__.py  ──>  from ...models import X
```

| file | holds |
|---|---|
| `config.py` | `goals.yaml` schema: `GoalsConfig` + `LLMConfig`/`CorpusConfig`/`ClassifierGateConfig`/… (imports `PrestigeConfig`/`OpenReviewConfig` from `enrichment_config.py`) **`USER_OWNED_KEYS`** is the allowlist of human-authored keys (research intent + the LLM connection + `university_access`) that `services/_common.write_user_config` persists to `goals.yaml`; every OTHER section is a validated code default, overridable via `ZS_*` env (`services/config_overrides`, see `docs/overrides.md`) — so `goals.yaml` stays intent-only and old files with technical sections still load. `ClassifierGateConfig` defaults to `enabled: true` / `model_name: lightgbm` (PROVISIONAL, cold-start safe — the gate no-ops without a golden CSV). Migrates a legacy flat `llm:` block into `llm_routing` when the latter is absent. `CorpusConfig` carries the hybrid Library-search knobs (`bm25_enabled`/`reranker_enabled`/`reranker_model`, all local); `PrestigeConfig` carries the cold-start author-reputation prior (`cold_start_author_lift`/`cold_start_max_lift`/`cold_start_gamma`) — a capped, raise-only fallback to authors' field-normalized standing before a paper has its own citation percentile. `PrestigeConfig.enabled` now defaults **on** (OpenAlex is keyless + cached + fails soft to neutral); `ZS_OFFLINE` forces it off in `services/_common.read_config` and `ZS_PRESTIGE_ENABLED=0` opts out. `QualityReviewConfig.shadow_claim_check`/`claim_check_model` gate the Phase-A MiniCheck encoder shadow (off by default; needs the optional `minicheck` dep). `QualityReviewConfig` also carries the **tier-aware deep-review cost** knobs (`self_consistency_runs`/`lean_self_consistency_runs`/`lean_max_text_chars`/`batch_goal_summaries`) — a provider flagged `lean_deep_review` (e.g. ollama, prefill-bound) uses the cheaper `lean_*` caps + one batched goal-summary call, while every other provider (incl. MLX — loopback but fast) uses the full settings. The tier is keyed on the provider's `lean_deep_review` flag, NOT `is_local` (loopback ≠ lean). `QualityReviewConfig.prewarm_on_startup_k` (default 5, `0` disables; env override `ZS_DEEP_REVIEW_PREWARM_K`) drives the **launch-time deep-review prewarm** (`services/library/deep_review_prewarm.py`). `QualityReviewConfig.render_on_tick_k` (default 3, `0` disables; env `ZS_QUALITY_REVIEW_RENDER_ON_TICK_K`) is how many TOP Today feed papers also get the full heavy paper RENDER per daemon tick (`services/triage/feeds/_tick._auto_render_slate`; built from the cached in-place review + cached PDF, no extra LLM; presentation HTML, audit JSON and figures only, no automatic Markdown sidecar). `QualityReviewConfig.quality_band_primary` (default off; env override `ZS_QUALITY_BAND_PRIMARY`) flips the order-time quality lift from grade-only (the shipped default) to band-primary — a Phase-2-measured arm read by the shared `services/_common.band_primary_enabled` and applied via `model/rank_blend.quality_bonus`. `QualityReviewConfig.self_verification` (default on) gates the second-pass over-claim check on critical met items; `use_docling` (default off, optional `docling` dep) swaps the fitz PDF path for structured tables + figure captions. `review_web_articles` (default **on**) renders a web article (blog/Substack/news with HTML full text but no PDF) to a PDF so the fleet can review it for RELEVANCE only — the review routes it to the `NON_PAPER` type (no scientific A–D grade; see `services/library/paper_type`/`quality_eval`) via the `web_article` acquire flag (`_pdf_acquire`'s web-article rung; needs the `browser` extra). `max_pdf_bytes` defaults to **50 MB** (`QualityReviewConfig` + `FullTextRefineConfig`) — figure-heavy clinical/Nature PDFs run >20 MB. `RecoverAbstractConfig` (default ON: `goal_sim_threshold` 0.45, `max_per_tick` 3, `min_abstract_chars` 120) drives the daemon's **acquire-before-score rescue** — a gate-rejected, abstract-less, high-goal item (prestige-journal RSS ships no real abstract) gets its full text fetched + re-scored before the verdict stands (see `services/triage/README.md`). `UniversityAccessConfig` (off by default) configures the review fleet's **browser** PDF fetch for non-arXiv/paywalled papers: `enabled`, optional `ezproxy_prefix` (blank = SSO/OpenAthens, the persisted session carries access), `login_url`, `browser_profile_dir` (blank = `data/browser_profile` via Settings), `headless`, `fetch_timeout_secs`, `cookie_browser` (reuse an existing browser's session via `browser-cookie3` — chrome/firefox/edge/brave; Safari is unreadable on macOS 15+/26), and `browser_channel` (default `chrome`: drive the REAL Chrome binary so its fingerprint matches the injected `cf_clearance` and aggressive Cloudflare publishers like Nature/npj accept it; `""` = bundled chromium) — needs the optional `browser` extra (patchright + browser-cookie3). |
| `enrichment_config.py` | `PrestigeConfig` (OpenAlex author/venue prestige — cold-start author-reputation prior `cold_start_author_lift`/`cold_start_max_lift`/`cold_start_gamma`; `enabled` defaults **on**, `ZS_OFFLINE` forces off, `ZS_PRESTIGE_ENABLED=0` opts out) + `OpenReviewConfig` (authenticated OpenReview peer-review SEARCH source: `venues`/`year_min`; creds are `OPENREVIEW_USERNAME`/`OPENREVIEW_PASSWORD` env, read at point-of-use by `integrations/openreview.py`, never this config; SIGNAL-ONLY — surfaces a `services/search` display chip, never a rank input; `ZS_OFFLINE` also forces it off). Split from `config.py` for the 500-LOC limit; re-exported via `models.config` |
| `access_config.py` | `UniversityAccessConfig`: optional persistent-browser/EZproxy settings, imported and re-exported by `config.py` |
| `feeds_config.py` | `FeedsConfig` — the `feeds.*` daemon section (tick cadence `daemon_tick_seconds`/`daemon_batch_size`, RSS refresh rotation `max_feeds_per_pass`/`max_new_items_per_feed`/`per_feed_timeout_secs`, read-marking `mark_processed_as_read` + `zotero_read_sync` (the Zotero unread-badge reconciler, default on), dedup toggles (`dedup_against_processed=None` follows `dedup_against_library`), outcome cadence, daily-selection scheduling, `inbox_collection_name`, `exclude_feeds`). System-owned, so every scalar auto-derives a `ZS_FEEDS_*` override — before this section, `feeds.*` was raw-dict passthrough that `GoalsConfig` silently dropped from goals.yaml, making the knobs unsettable. Split from `config.py` for the 500-LOC limit; re-exported via `models.config` |
| `providers.py` | per-stage LLM routing: `ProviderConfig`/`ProviderType`, `StageModelConfig`, `LLMRoutingConfig`, `resolve_stage()` (stage → effective provider+model, inheriting `default`). `ProviderConfig.is_local` flags loopback endpoints (→ serial triage concurrency); `ProviderConfig.lean_deep_review` opts a provider into the cheaper deep-review tier (independent of `is_local` — set on ollama, left False on MLX); `ProviderConfig.max_sub_concurrency` caps how many deep-review SUB-calls (rubric samples / per-goal summaries) fan out in parallel on a remote provider (`None` → fall back to `TRIAGE_JOB_CONCURRENCY`; ignored for local providers, which stay serial). `ProviderConfig.temperature` (default `0.0`, the previously-hardcoded deterministic-triage value) is threaded into the openai-path `build_llm`; the native Anthropic adapter ignores it (Opus rejects the param). `ProviderConfig.thinking_effort` (`off`/`low`/`medium`/`high`, default `None` = leave untouched) is translated per backend dialect by `services/llm/thinking.py` (anthropic → thinking budget; plain OpenAI → `reasoning_effort`; vLLM/qwen `chat_template_kwargs` → `enable_thinking` on/off — graded collapses there). `ProviderConfig.thinking_on` (computed: `thinking_effort != "off"`) is what the deep-review DIGEST reads to decide `enable_thinking` — the digest reasons by default (measured NEEDED) and a provider set to `thinking_effort: off` opts it out, with no separate toggle. `ProviderConfig.num_ctx` (default `None`) auto-derives for LOCAL providers in `services/_common._derive_local_num_ctx` (`max_text_chars/4 + max_tokens` ×1.25 headroom — ollama's own ~2–4k default silently truncates a long deep-review prompt) and is forwarded as `extra_body.num_ctx`; remote providers stay `None` (they size their own window). `ProviderConfig.keep_alive` (default `None` = off) is the ollama warm-hold (seconds a LOCAL model stays loaded after a call); a LATENCY knob, not correctness, so it is OFF by default and NOT auto-derived (pinning a model resident grows steady-state RAM — unsafe on a memory-constrained Mac), forwarded as `extra_body.keep_alive` only when set explicitly (PROVISIONAL, GAP §G6). `ProviderConfig.structured_output` (default `False`) opts a provider into decoder-level JSON (`response_format json_schema strict`) for the digest — MEASURED trade-off (forces enum/range + reasoning survives, but completeness drops 1.00→0.62 and needs a roomy `max_tokens`); enable only for models prone to invalid JSON (see `docs/internal/knowledge_map.md` §D). Pure data + lookups — no env reads, no client building. |
| `triage.py` | `SummarizeRequest/Response`, `TriageResult/Dimensions`, `QualityReview`, `PaperParameters` (verbatim full-text dataset/baseline/sample/metric/architecture/validation extraction), `PaperDigest` (the single `read|skim|skip` action plus targets, time, original value, optional parameters, and grounded low/moderate/high writing friction; high friction caps read to skim; old rows load through defaults), `GoalSummary` (grounded per-goal evidence), `QualityEval` (reference-free checklist/band, evidence, flags, agreement and optional MiniCheck shadow), `ProposedVerdict` (deterministic display-only priority suggestion with optional per-review freshness metadata), batch/corpus/calibration models |
| `research_feed.py` | Source-neutral candidate/triage/profile/engineering-card contracts, controlled topic/project-use literals, absolute HTTP(S) artifact validation, and strict original-only `TelegramPost` / small `TelegramNewsVerdict` news contracts (imported directly from this module). `triage.MethodAndCode` is the optional exact-source-URL method/artifact block carried by summaries. |
| `zotero_actions.py` | Strict/frozen closed default-review `upsert_note` proposal and note payload, with native canonical database path/device/inode/actual user-library ID plus item/publication/source/body binding and full preview SHA-256. Imported directly, not an arbitrary Zotero patch schema. `PendingApprovalPreview` binds each queued ID/item/type/exact payload/edit revision/native target and the original generated digest when present. Required `PendingChangeApplyRequest.previews` transports common outer hashes; ID-only and old generated-inner-hash approvals are refused. `PendingChangeMutationRequest` remains the Reject shape. |
| `research_feed.py` | Source-neutral candidate/triage/profile/engineering-card contracts and unchanged canonical `TelegramPost` / `TelegramNewsVerdict`; strict `TelegramPage`, shared live-ID validation and application username normalization are direct-module-only, absent from the unchanged root exports. `triage.MethodAndCode` remains the optional exact-source-URL summary block. |
| `paper_keys.py` | Pure lexical paper-key guard shared by question API/storage and paper-state rendering: reject blank, dot/dot-dot, slash, backslash, and NUL while preserving accepted keys exactly. No filesystem/existence or PDF/review/Add check. |
| `api.py` | HTTP request/response + write-path models (`*Request/*Response`, `AppState`, Zotero/pending shapes) |
| `setup.py` | the FROZEN `/api/setup/*` contract: `SetupStatusResponse` separates saved `configured` state from operational `ready` health (+ nested `ConfigStatus`/`LlmStatus`/`PathStatus`/`PathEntry`/`ZoteroStatus`/`ClassifierStatus` + a `subsystems: list[SubsystemStatus]` runtime-readiness array, additive — a not-ready entry names the down subsystem + reason without affecting the top-level `ready`), `DetectedZoteroDir`/`DetectZoteroResponse`, `UpdatePathsRequest`/`UpdatePathsResponse`, `ValidateConfigRequest`/`ValidateConfigResponse` (+ `FieldError`/`ConnectionResult`). SECURITY: `api_key_env` is only ever an env-var NAME; key presence is the BOOL `api_key_present` — no model here carries a secret value |
| `__init__.py` | re-exports each module's declared `__all__` (the public `models` surface) |

`GeneratedNoteProposal.target=None` is solely a legacy display/rejection state,
never native-write authority. The service's new-proposal builder requires a strict
`ZoteroTargetIdentity`; approval checks the actual selected writer and does not
backfill an unknown target or infer it from an item key. Native file identity is
independent of unrelated edits, not a content hash or authenticated account ID.

Apply maps contain exactly the selected IDs with lowercase 64-hex hashes. Invalid
or absent maps fail HTTP422; current-shaped stale/unknown/changed-target/state
requests fail409. The list DTO adds nullable `approval_preview`; missing native
writer and legacy unknown targets do not fabricate approval. Version1 includes
all payload fields, not rendered summaries. Hash equality is neither authentication
nor proof of human attention. Failed retry and applied replay retain effect identity;
uncertain `applying` is never silently reset.

`GoalsConfig.llm_enabled` is user-owned. `LlmStatus.enabled=false` is the
deliberate ML-only state, not an unreachable-provider error.

Draft `ConnectionResult.load_receipt` forwards admission identity/reason,
attempted versus unsubmitted and callback/ownership state. It is diagnostic,
not stored configuration, load consent or a remote-finality guarantee. Requests
gain no UNKNOWN exception.

**Boundaries:** depends only on `pydantic` + `domain`. Nothing here imports
`services/`/`storage/`/`api/`.

`SummarizeResponse._corpus_fast_rejected` is private, per-response in-memory
provenance, defaulting false. Only the corpus-prefilter producer stamps it;
generated JSON and tags cannot supply it. It is absent from public schemas and
dumps. JSON reload defaults false rather than reconstructing machine authority
from presentation fields; the existing durable decision records the outcome.

`CorpusItem.doi` defaults to an empty string for older imports. It carries
identity metadata independently of title/abstract embedding content, enabling
self-exclusion when the same paper appears under another title.

`ResearchEngineeringCard.worth_reading` includes `unknown` for a withheld reading
decision. It is distinct from an assessed `skip`; existing `evidence_gaps` carries
the policy's reason without a second confidence/action field.

`ResearchProfile` admits schema version 1 only. Its strict integer shortlist/card
types are also used by `research_feed.parse_run_budgets` at CLI/service entry:
shortlist 1–100, cards 1–20, source rows 1–5,000 and wait timeout 1–86,400 seconds.
Only omitted shortlist/card overrides use profile defaults; zero is not omission.
The same profile/version gains `telegram_channels=[]`, without a second config or
live-channel authorization. Telegram posts require explicit version 1, exact string
text/caption/locators, numeric SQLite-range identities, aware original date strings
and an explicit skip reason for empty text. Extra fields/embedded assessments are
forbidden. News verdicts distinguish keep/drop/uncertain and require reason/quote,
with a summary for keep. The service checks declared interests and literal original
quote membership; this is not semantic validation or calibrated relevance.

`TelegramPage` requires a strict boolean `complete` and at most 100 canonical posts
in a tuple; false is representable but not committable. Completeness attests only
the requested page, not exhausted history. Storage revalidates mutable/bypassed
instances through these same canonical validators, without another original parser.
New page/helper/type-alias names require explicit `models.research_feed` imports;
`research_feed.__all__` and `models.__init__` remain unchanged.

`normalize_telegram_username` strips outer whitespace and zero/one `@`, accepts
1–32 bare ASCII alphanumeric/underscore characters with at least one letter, and
returns lowercase. Short and digit-leading mixed names remain admissible. Invalid
values raise bounded errors without echo. This application syntax is not API
validity or consent; future collection retains configured spelling in its receipt.
`LiveTelegramChannelId` / `validate_live_telegram_channel_id` require a nonboolean
signed SQLite integer below -1000000000000 for new live state only. Saved identities,
profile/schema, budgets and serialization stay unchanged; historical positive IDs
are not converted or declared equivalent to live channels.

`MIN_MAP_CHUNK_CHARS` is the shared 1000-character validation/recovery floor; the 8000-character default is unchanged.

## Declared provider capabilities

`LLMCapability` is closed to `text` and `native_json_schema`, the two paths the
current adapters implement. `ProviderConfig.capabilities` is an optional, exhaustive
declaration for the models selected through that profile. Omitted/null retains only
adapter-known text; an explicit empty list declares neither capability. Optional
support is never inferred from model names, JSON-via-prompt parsing or configuration
such as `num_ctx`. Profiles serving different model capabilities need separate,
conservative declarations; there is no model inventory or probe.

`available_capabilities` intersects declarations with enabled adapter paths. Native
JSON schema needs an explicit declaration plus OpenAI-compatible `structured_output`
or the native Anthropic `messages.parse` path. Declaring it does not enable wire
options; legacy `structured_output` behavior is unchanged when declarations are absent.

`resolve_stage` preserves independent provider/model inheritance, then requires text
for the existing configured feed workflow. It never tries another provider/model.
`ResolvedStage.required_capabilities` retains that code-owned requirement in memory;
`CapabilityMismatchError` exposes only diagnostic route/capability metadata, not
credentials or `extra_body`. The factory rechecks before resolving credentials.
Other production stages and caller-supplied clients are not migrated by this slice.

Unset declarations are omitted from nested Python/JSON serialization, and transient
requirements are excluded, preserving legacy config/resolved-stage cache identity.
Explicit declarations round-trip. This is declared admission, not measured endpoint
support, context capacity, setup readiness or scientific-quality evidence.

## Experimental interpretation producer contract

`interpretation.py` is imported directly only for an explicit experimental C
collector; it is **not** re-exported by `models.__init__`. `PaperDigest` and its
normal schema/serialized keys remain unchanged. `InterpretationDigestResponse`
adds typed optional `InterpretationDraft` / `InterpretationUnavailable` content
at the generation boundary, then returns a plain base digest from the service.

Items reference current `/tldr` or `/key_findings/<index>` text rather than
copying it. Scientific fields are `inference_strength`, `rationale`, `scope`,
`uncertainties` and `unsupported_extrapolations`; `basis_refs` names existing
methods/limitations/key_weakness/parameters fields. Empty scientific text means
unknown; missing mandatory item fields or malformed types mean unavailable.

The service alone stamps `origin=ai_generated` and
`scientific_verification=unverified`. Shape/pointer validation is machine IO,
not passage entailment, causal warrant, contradiction or author approval.
Malformed optional content becomes an explicit typed unavailable alternative,
without inventing mandatory base fields or a scientific-support state.

### Closed interpretation references and whole-value identity

`interpretation_references` in `models.interpretation` is the single closed map
used by the explicit C producer and private audit/cache binding. Findings are
`/tldr` or canonical zero-based `/key_findings/<index>` references. Basis pointers
are fixed methods, limitations, key weakness, whole parameters or their declared
parameter fields; arbitrary paths and copied finding text are not admitted.

Missing, null or empty values cannot supply reference evidence. Real false values,
including `parameters.external_validation=False`, remain valid content. Reference
presence does not establish scientific support: accepted native checks and exact
canonical whole-value/payload identity are separate service requirements, and
false cannot match a type-changing zero.

The response fields, typed optional alternatives, AI-generated/unverified state
and plain `PaperDigest` projection remain unchanged. These types are still
imported directly only for explicit C code; no `models.__init__` export, public
flag or downstream consumer activation is introduced. A stored condition marker
is comparison evidence, never import/schema authority or scientific approval.

## Explicit feed response contract

`research_feed.FeedReviewContext` snapshots the versioned profile and exact source
key. Only explicit code input selects `FeedDigestResponse`; normal PaperDigest
schema/keys and the separate private interpretation condition remain unchanged.
`FeedAssessment` contains strict project records plus nullable typed reproduction
and impact blocks. Actor/verification authority is never model-supplied.

Applicable project records require an exact project/source key, closed use kind,
mechanism, component, actual change, experiment and original quote. No-fit uses
explicit `not_applicable`; unknown has no use/change/experiment. The service checks
configured membership and original quote coordinates. Cards retain typed project
records, quote spans, optional impact/reproduction source warrants and separate
`not_assessed`, `invalid`, `partial` or `assessed` state/reason. Ideas reuse actual
record changes and experiments. Literal membership and valid schema do not certify
semantic relevance or usefulness: producer origin stays AI-generated/unverified.
Unassessed compatibility scores are zero with a null impact assessment, not observed
zero. Missing optional content cannot fill absent mandatory base reading fields.
## Injected Telegram collector values

`ResolvedTelegramChannel` and `TelegramTransportError` are consumed by the explicit
async `services.research_feed.telegram_collect.collect_pages` seam. Import them
directly from `models.research_feed`; neither is added to `research_feed.__all__`
or the package root.

`ResolvedTelegramChannel` is strict/extra-forbid with only
`channel_id: LiveTelegramChannelId` and `public_broadcast: bool`. It carries no
username, access hash, SDK object or credentials. The service revalidates current
instance fields, including mutable/constructor-bypassed values, through this same
model. Explicit false public_broadcast yields channel_unavailable without binding
or fetching; malformed values are protocol errors, not inferred account failures.

`TelegramTransportError(code, *, retry_seconds=None)` admits only missing_session,
session_unauthorized, revoked_session, network_error, channel_unavailable and
flood_wait. Only flood_wait requires positive nonboolean integer retry_seconds;
other codes forbid seconds. Invalid constructor values raise bounded ValueError
without echo. Exception args contain only the validated code, not an upstream
message. The collector checks current error fields through this constructor before
using them, so mutation cannot silently invent a failure/default classification.

These are application I/O contracts, not SDK error mappings, session readiness,
public-channel validity proof, content permission or scientific/human acceptance.
No source framework, auth envelope or additional profile/schema setting is added.
