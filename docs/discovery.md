# Find new relevant — server contract

Server contract for the integrated provider/session/shared-UI feature. Controlled
regressions establish machine boundaries, not live-provider or scientific quality.

## One explicit outcome

`GET /api/library/related/{item_key}/discover` reads saved admission, processing
boundary, private-context availability/count and a context fingerprint. It does
not construct model/provider clients, use saved-paper hybrid retrieval, or write
sessions. Native readers retain their normal read semantics, including SQLite's
ordinary read-side behavior; this is not a filesystem no-WAL-side-effect promise.

`POST` at the same path requires `context_fingerprint` (64 lowercase hex), accepts
an optional same-shaped `consent_token` and strict boolean `retry=false`, and
forbids extra fields. The response uses the existing Search session `id` and a
controlled `discovery` projection. No client-supplied query or authority override.
A current explicit app approval or actual current native-library presence admits
the seed. Detail existence, automatic selection, a key's shape, and a remembered
materialized key alone do not. Current app decisions are selected before filtering
and without the general browsing cap; automatic kept families still exclude
external candidates. Native membership uses live user-library keys and DOI/URL
fields, not a whole-library note crawl. Retained family identifiers and all-alias
coverage reuse Search's existing identity owner. Unknown is not established new;
equal titles with different identifiers are not merged.

## Privacy and evidence

This bounded version schedules only existing OpenAlex semantic/lexical retrieval,
using allowlisted public title/abstract/identifiers. It does not search arXiv or
Europe PMC; ordinary topic Search still does. Unscheduled plan fields are cleared.
OpenAlex quota/client failures remain visible, not covered by a hidden fallback. Existing public relevance rankers are reused;
Find does not invoke intent models, default review dependencies, Search screening,
agentic refinement, full-text review or native Add.

Without a token the result is public-only. Explicit optional consent sends the
complete attributed **seed** context to the exact configured review boundary,
using its actual resolved-client identity check. External candidates are addressed
by persisted candidate IDs and have unavailable local comments, never fabricated
observed-empty reads. Full RU/EN notes, negative context, revisions, deletions and
uncertain native authorship retain their existing semantics. Generated notes and
app mirrors are excluded; highlights are paper evidence rather than preferences.
The existing character ceiling rejects oversized input instead of clipping notes.

Only typed relevant-and-compatible assessments recommend contextual results.
Submitted comment IDs are separate from model-cited IDs. Two-paper quote membership
is validated, not semantic entailment or scientific truth. Default recommendations
are capped at three while the bounded retained candidate pool remains available.
No assessment/evidence result is a full-text review or a quality qualification.

## Reuse, freshness and effects

One fixed session-owner discovery lock serializes the synchronous bounded flow;
private work additionally shares the Related assessment lock. One transient active
identity distinguishes live work from restart-interrupted claims. Strict lookup
uses the exact context namespace, guarded loads and unique random session suffixes.
Corrupt matching files and multiple live matches fail rather than cause new work.
Only existing deletion tombstones justify disappearing-file skips. Session deletion
does not wait on the discovery lock; late saves cannot resurrect deleted work.
These are single-process/atomic-file-visibility guarantees, not distributed or
fsync/power-loss durability claims.

Repeated clicks reuse successes, partial results and failures. Failed/interrupted
or partial-provider work reruns only with explicit retry. A later comment opt-in
or failed-assessment retry reuses a current public pool without another federation.
Attempts retain earlier receipts/results. Reviewed sessions are not re-reviewed by
Find. GET can project abandoned work as interrupted without doing recovery work.

Actual seed metadata, attributed seed context/aliases, current kept/native membership,
selected configuration/corpus/encoder/budgets/boundary and source identities bind
the context. A separate candidate fingerprint binds retained addresses and public
metadata, not review fields, ordering or retrieval times. Checks follow providers,
follow acquisition of the private-work lock, follow the model, and precede new
Review/Add effects. Unrelated library-native-note edits are not inputs; seed edits
are. Necessary read failures produce unverified state, never current empty inputs.

Generic session serialization is safe by default and retains only an unverified
Discovery marker; only the existing store opts into full private serialization.
Projected DTOs cannot restore durable sessions or silently become ordinary Search
clones. All HTTP session responses, including screen, use controlled projections;
Review kickoff returns a status receipt only. This frozen base has no HTTP refine
or clone action, and none is introduced here.

Search GET performs no model/provider refresh. Stale/unverified projections hide
private assessment items, excerpts, IDs and seed comments, keep public metadata
visible with qualification and disable new actions. Provider receipts distinguish
successful empty from failed/not-attempted/unknown work; partial empty results are
not genuine empty. Provider receipts are request-local under the frozen adapter
contract, not inferred from candidate counts.

Explicit Review requires current inputs and a `discovery_ready → reviewing` CAS.
The shared automatic kickoff rejects discovery unless explicitly invoked by the
Review endpoint; unclaimed workers reject before review-client construction.
Direct unclaimed review is rejected and discovery skips agentic refinement.
Ordinary Search still auto-starts review. New Add checks freshness within the
existing check/write/stamp operation, without recursive session-lock callbacks.
An already-materialized replay remains effect-free even after context changes;
it does not certify old discovery freshness. No forced review-before-Add gate is
introduced. A new native Add changes membership, so the next GET may be stale.

A retained unstamped Add plan may be explicitly reconciled despite stale/unverified
Discovery. The existing native operation runs in enforced existing-only mode:
URI read-only/query_only, no materialization, backup, pruning or mutating PRAGMAs.
Only a fresh exact-key, empty-steps/no-backup ACK finalizes the plan. Missing,
malformed or failed ACKs refuse; they cannot fall through to a new creation.
This acknowledges the current no-op, not success of any earlier raised invocation.
Plan/coverage conflicts still refuse. No evidence freshness, later user-edit repair,
live/untrashed membership, physical-file identity or cross-DB atomicity is certified.

## Verification boundary

`tests/test_discovery_server.py` and `tests/test_discovery_server_races.py` author
real route/session/isolated-SQL controls with inert model/provider seams.
`test_discovery_integration_regressions.py` also exercises real provider adapters
through inert HTTP, an exact ordinary-session wire fixture and app-linked native
identifier families, including transitive IDs and comment edit/deletion freshness.
`test_discovery_browser.py` checks the built React flow through real API/SQLite
at mobile/desktop widths, with inert providers and no native writes. Existing
Related consent, Search identity/coverage and materialization controls still apply.
`test_discovery_recovery_joint.py` and `test_discovery_existing_only.py` exercise
native commit/final-save gaps, explicit current no-op ACKs, malformed receipts,
execution-path refusal, user-library scope and committed WAL visibility.
These do not qualify live providers, native-user correctness, semantic usefulness,
saved-comment recall or the owner-labelled three-arm evaluation.
