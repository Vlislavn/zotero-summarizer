# Find new relevant: shared UI contract

This is the UI part of one complete discovery feature, not an independently
releasable UI change. Deploy only with the matching admission/discovery/session
routes and provider receipts. Controlled DOM/build/browser checks do not establish
live-provider, native-user or scientific usefulness.

## One action, two privacy boundaries

1. Open a paper through the existing shared Today/Library `/paper/:itemKey` page.
   Related reads discovery metadata independently of **Show related saved papers**.
2. Only `admission.state=saved` and a usable context fingerprint enable **Find new
   relevant**. Unknown admission is not saved. A key shape or local candidate count
   does not establish membership. Read errors expose explicit Retry; there are no
   focus/reconnect/automatic retries. Unavailable fingerprints expose their reason
   and an explicit eligibility refresh.
3. The folded processing disclosure names the configured provider/model/endpoint.
   By default the action sends `consent_token=null`: public metadata only. The
   optional seed-comments checkbox requires explicit confirmation to that exact
   boundary. Declining leaves public-only discovery available. External candidates
   have no saved private comments; other library papers' notes are not inputs.
4. Click Find. The POST includes only `context_fingerprint`, `consent_token` and
   strict `retry`; no query/private text is assembled in the UI. Providers receive
   public metadata, never private seed comments. Full context/results stay locally
   persisted by the server. Assessment is experimental, not a claim of usefulness.
5. Only the current owner can navigate to `/search?session=<response.id>` after
   completion. Primary note/verdict ACKs already remount Related; old metadata GETs
   and discovery POSTs are aborted/fenced on replacement or navigation. Aborting a
   browser request is not a promise that server work stopped.

The existing saved-related comment experiment, ACK boundary, loading/error privacy
and explicit refresh remain intact. Discovery does not start saved-related reads,
Review, refinement or Add automatically. A failed Find can be explicitly retried;
refreshing eligibility clears consent. From Search, **Return to seed to explicitly
retry discovery** carries `retryDiscovery=1` through the existing router. The new
seed owner reads current metadata and starts with unchecked consent. The URL alone
never POSTs; another Find click is required.

## Search ownership and honest display

A URL session pointer wins over optional tab storage. Storage denial cannot prevent
handoff. GET failure retains the pointer and offers **Retry session status**, never
silently clears it or calls discovery again. Every load, screen, Review and Add
completion belongs to one current session/request; navigation and unmount invalidate
old callbacks. Poll cancellation invokes the returned function and aborts active reads.
The existing 15-second session-read timeout remains with caller cancellation.

Discovery displays at most three server-selected recommendations in their supplied
order, with every other retained observation under a native disclosure. Public
metadata relationships are not full-text quality judgments. `{}` means **Not
reviewed**; only an observed `needs_full_text` review records acquisition failure.
Unknown membership is **not established new**, regardless of candidate count.

Retrieval states are `observed`, `partial`, `failed`, `unknown`, `not_attempted`.
Only server empty reasons distinguish successfully empty providers from all-known-
saved observations. A zero-row partial/unknown run is not genuine empty. Request-
local OpenAlex semantic/lexical receipts expose channel, status, count (including null)
and fixed code. This version does not query other providers; ordinary Search does.
Assessment states are `not_requested`, `assessed`, `no_comments`, `no_candidates`,
`disabled`, `budget_exceeded`, `context_unavailable`, `failed`, `interrupted`, `stale`.
Session pending/running/ready/failed/interrupted are displayed separately, not called
review success. No counts/errors are used to infer provider/model availability.

Current evidence includes metadata quote address/field/span, a metadata-bound model
sentence, attributed seed comment excerpts and separate submitted/model-cited IDs.
Quote membership is not independently verified entailment. Comments are reader
context, not paper evidence; reported use does not establish causal influence.
Stale/unverified projections and unsuccessful/in-flight status reads hide private
assessment text, comment bodies/IDs and recommendations. Public retained metadata
remains available, qualified, with new Review/Add disabled. A retained Add plan
instead exposes **Reconcile retained Add** on that same action. Only an explicit
click may obtain the server's existing-only native ACK; it never refreshes private
evidence or authorizes a new stale native effect.

**Review retained candidates** calls the existing explicit review API, then reads and
polls only that session. Discovery poll failure stops automatic retry and states that
the server may still be working; explicit Retry resumes reads. It is neither labelled
cancelled nor complete. Add targets the persisted candidate ID and current session,
then obtains server freshness rather than optimistically certifying other results.
Server preflight remains the authority before new Review/Add effects. No forced review
before Search Add is introduced. Ordinary topic Search still uses its automatic
server review and transient poll retry behavior, existing sorts and collection picker.

## Source controls and open acceptance

DOM controls use real components/wrappers with inert fetch boundaries:

- `RelatedPapers.discovery.test.jsx`: admission, independent/no-auto-retry reads,
  actual consent bodies, refusals, navigation/AbortSignal, actual page primary ACK,
  real Search retry round-trip and a new click before retry POST.
- `Search.discovery.test.jsx`: URL/storage denial, all typed states, three/default
  versus retained rows, unreviewed versus missing full text, unknown membership,
  evidence/attribution, private hiding, GET retry, late loads/Review/Add, post-Add
  freshness, explicit review polling and ordinary screen cancellation.
- `libraryApi.discovery.test.js`, `searchApi.discovery.test.js`,
  `searchPolling.discovery.test.js`: exact DTO mapping, typed HTTP refusal,
  cancellation plus timeout, discovery stop-on-error and ordinary retry preservation.
- Existing Related/page ACK tests retain every assertion; only inert discovery-GET
  fixture exports/branches were added to accommodate the independent metadata read.

The ordinary Search fixture is captured from actual session HTTP serialization
and checked by a producer-side test; its explicit empty Discovery object must not
enter the Discovery branch. A built-browser regression also exercises real API/SQL,
partial inert provider receipts, stale action refusal and ordinary Search restoration.
Live provider/model/native-user usefulness, saved comment-only recall and the
owner-labelled three-arm study remain open. Run all integration checks and review
richer later Search interfaces before merging.

Simplification: reuse existing session DTO/API, router, keyed Related owner, comment
attribution renderer, review/Add commands, polling and native disclosures. No new
store, provider selector, wizard, global navigation, theme or dependency was added.
