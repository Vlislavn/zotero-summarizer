# services/llm — provider-aware client construction

`build_client_for_provider(..., local_only=True)` is an opt-in ordinary inference
privacy boundary, not Setup verification or load approval. It refuses nonliteral
loopback, unspecified addresses, credentials-in-URL, model-file URLs and non-OpenAI
adapters before credentials/client construction. Every HTTP send is rechecked;
the shared no-proxy/no-redirect HTTPX helper is reused with cache/retries disabled
and caller-owned close. Combining `verification=True` is rejected. Existing
callers keep their defaults. No locality flag certifies model or hardware safety.

`goals.yaml:llm_routing` resolves each stage to a provider/model;
`factory.resolve_stage_client(runtime_state, stage, ...)` lazily builds clients
using the cache, lock and invalidation generation held by `RuntimeState`.

```text
llm_routing → resolve_stage → ResolvedStage → factory → LLMClient
                              feed · backlog · deep_review
```

Verification binds an explicit attempt/provider/model owner before credentials.
Each prompt requires joined expected/native/pre-send/terminal evidence; probe
receipts use their own request slice, not a completed sibling. Native success is
not hardware admission. Local UNKNOWN still refuses before construction (except the memory-checked probe above), ordinary
hosted diagnostics keep independent-model continuation, and client close is cleanup
rather than finality. Safety receipts stay in the existing selected Doctor file.
Explicit `request_timeout_seconds` lets an owned full-review caller select a finite
request deadline, capped by `Settings.summary_timeout_seconds`. Omitted verification
deadlines keep the tiny-probe cap. Invalid/nonfinite budgets refuse before credentials;
this does not waive local admission, native ownership, currentness or approval.

## Connection task

`connections.py` owns explicit exact destination/auth approval, generated provider
names/credential slots, inactive registration, saved-server model selection, owned metadata, manual IDs, effective
impact and guarded explicit task/default assignment. A selection changes only its
named targets; named stages become complete provider/model pairs. Default-only
commands retain their previous meaning. Preview/apply/readback bind model, targets
and corrections, and remaining provider consumers are computed after assignment. `connection_impact.py` resolves each stage
field independently and includes shared provider/slot dependencies and Telegram's
separate strict-loopback route, reusing Telegram's actual `resolve_local` admission.
Missing/rejected Telegram routes retain a reason/detail without blocking an
unrelated default change or weakening no-fallback. Crossed partial pairs require explicit prior-pair,
or new-default corrections (or cancel), never unverified mixed-pair retention, plus a refreshed preview;
readback compares the intended explicit stage fields too. No silent pin/reset occurs. No old provider or stage option is changed/deleted.
`connection_state.py` stores only task receipts below `Settings.data_dir`, not a
second provider/client registry. Intent precedes config publication so readback can
compare the exact provider/default from disk after a lost acknowledgement. A failed
receipt/config write is not a rollback; restart/reload is required if another
process changed disk behind this runtime. Missing initial config stays a draft until
a real explicit model builds a complete first config. Unchanged protocol/base/auth
reuses the saved provider identity/options instead of appending an indistinguishable
record; only changed destinations/auth ownership create candidates. Approval lookup
uses a derived provider-name → task-receipt pointer for reused names, not a second
provider/client store; exact identity and current revisions still authorize lookup/send.
A new generated credential slot can be deliberately established through the same
separate consumer-preview/keyring/receipt owner, even before first config creation. Unknown remote bootstrap
catalogs require manual-ID/default save first; existing invalid files are preserved.

The shared nonwaiting configuration lock serializes app-owned config, credential
and Telegram-source saves. Full-config HTTP clients must round-trip `_revision`;
scoped edits compare config/disk/calibration/auth-marker/profile revisions before
publishing. Concurrent owners get 409, never a blocking flock on the event loop.
This does not serialize uninstrumented external writers, detect arbitrary external
keyring/env edits, cancel existing model calls or make keyring/config transactional.
`connection_credentials.py` previews every shared consumer; the Doctor owner also
holds Telegram's existing operation lock before replacing a slot, preventing edits
between its config snapshot and later credential lookup. Active Doctor verification
refuses replacement before taking Telegram/configuration locks, rather than waiting
while owning unrelated publication. Catalog similarly releases publication after
snapshot/credential admission, rechecks at pre-send and before retaining results;
late metadata cannot publish over changed configuration. Safe catalog HTTP/status,
timeout/transport/malformed observations retain manual-ID recovery without claiming
login or model operation. In-flight non-Telegram
clients may keep their prior credentials. Auth receipts distinguish stored from
unknown, never infer rollback from cancellation or failed transport.

`ProviderConfig.auth_mode=none` returns None **before** keyring/env lookup. Installed
OpenAI requests use SDK request-local auth omission plus owned no-proxy,
no-redirect transport. Native Anthropic no-auth uses HTTP inside the same adapter,
reusing pure SDK schema/response parsers without constructing the SDK client: its
constructor otherwise inspects an ambient active-config pointer even with empty
credentials. Ambient credentials, custom headers and base URLs cannot own these sends. Native default base remains persisted as null but resolves to the
official API explicitly. Legacy profiles/ordinary local credential fallback remain
unchanged. The application LLMClient interface is synchronous; no-auth OnPrem
supplies a rejecting async placeholder so LangChain cannot silently construct an
ambient-auth client for an unsupported async call. Viewing/registering/selecting
never runs inference, downloads weights or certifies hardware safety. Explicit
Doctor verification retains UNKNOWN refusal and existing one-attempt behavior.

`connection_approval.py` is the one consent owner for compatibility metadata and
verification too: saved membership is insufficient for secret-bearing custom
remotes. Receipt identity includes protocol, exact base, slot AND auth mode; a
no-auth receipt cannot authorize a credential-bearing request. Approval stays valid
across unrelated saves: `approve()` records an HMAC-SHA256 fingerprint of the slot's
current key (`credential_fingerprint.py`; random salt in `data/credential_fingerprint.salt`,
0600; never the key) in the approval pointer, and `require_approval` then checks exact
identity + that fingerprint (key absent or replaced = needs approval). A pointer without
a fingerprint (legacy, or no key at approval time) keeps the strict whole-config/auth/
profile revision check until re-approved. Apply/edit/test flows still fence with
`require_revision`; per-send fences are checked before lookup/send. Preset/local
non-secret/declared-none admission and ordinary legacy inference remain. An explicit
Doctor action is not a bypass. Policy refusal is needs-approval/unknown with direct
AI connection recovery, never unreachable/wrong-model/hardware-unsafe. CLI can
consume a real current task receipt through its selected persisted configuration;
unapproved custom routes no longer gain discovery permission just by being saved.

`connection_test.py` resolves the current saved default or explicitly selected
saved stages, never an unsaved draft. Selected stages share the existing serial
`check_routing_stages` probe/cache, deduplicated only among selected provider/model
pairs, with target identities and per-stage results in a revision-bound receipt.
Both paths reuse `SetupAttempt.run`, `probe_provider`, load admission, owned transport and native
request/terminal inventory. It uses the existing named remote/local probe budgets;
no second runner, fallback or automatic retry. Configuration admission is short:
the existing attempt's pre-request hook checks the captured revision before
credential lookup (inside the common lock) and at native pre-send entry. Network
completion never owns the configuration lock; unrelated saves remain available.
Already-started requests retain their snapshot; a changed revision makes the
returned result stale. The pre-send observation is not atomic remote acceptance.
A timed-out callback retains Setup's request exclusion/HOLD until return;
close is not remote cancellation. Responses are from a fixed nonprivate tiny prompt
and bounded for display. Local UNKNOWN refuses before credential/client work. AI-off
returns disabled without inference. Default testing does not cover stage overrides;
selected-task testing does not imply structured-output, PDF or real-paper success.
Viewing, registration, listing, impact and selection saving never invoke a probe.

## Files

- `factory.py` owns stage-client resolution: under the runtime lock it copies the
  effective routing snapshot, checks `expected_identity` before cache/credentials,
  and keys clients by stage, thinking variant and snapshot fingerprint. Construction
  runs outside the lock; only the same invalidation generation may publish a
  successful build. Failed builds are not cached. It also dispatches
  OpenAI-compatible/native Anthropic clients and resolves secrets from the keyring
  with env fallback. OpenAI-compatible transports receive
  the process `SUMMARY_TIMEOUT_SECONDS` request deadline; its optional
  `enable_thinking` overrides only clients already advertising that capability.
  A verification/light-call override sets an active plain low/medium/high
  `reasoning_effort` to `none` (#141: `low` still spent 3-11k hidden reasoning
  tokens per light call on a remote reasoning model; `none` spends 0 and keeps review output).
  Explicit configured off stays off; chat-template thinking can be disabled.
  No unsupported parameter is injected and generator configuration is unchanged.
  Declared `structured_output` applies schema constraints to every Pydantic call
  through the shared adapter, including CLI calls and verifier/correction passes.
- `thinking.py` translates effort to Anthropic budgets, OpenAI
  `reasoning_effort`, or qwen/vLLM `enable_thinking`. Explicit `off` sends
  `reasoning_effort: none` in the plain OpenAI dialect; omitting it would preserve
  the server's potentially enabled reasoning default. Unset effort remains a no-op.
- `step_history.py` reads per-step speed and last success from logs the app already
  writes (`[dr …] review done in Ns` in the app log for deep review; completed
  `triage_jobs` for backlog; feed has none) for `GET /api/llm/step-history`. Runs are
  not tagged with a model, so after a model change they describe the earlier one.
  `deep_review.median_tok_per_s` = completion tokens per call-second (prefill included)
  over the recent cached reviews' `timings` — the endpoint's decode speed, which varied 4×.
- `local_test_reachability.py`: with an approval policy configured, or outside the
  Settings→Test/Doctor probe, load admission still refuses a local request before any
  prompt (an external daemon never yields owned-runtime evidence). Only the Settings Test (`connection_test`) passes
  `probe_provider(..., memory_probe=True)` (default False elsewhere): with no policy a fresh RAM/swap reading may admit ONE short Test
  (`services/setup/local_memory_admission.py`; reply capped at 16 tokens, probe deadline 60 s,
  re-read before the request in `_admitted_request`, model unloaded afterwards unless it was
  already resident; `admission.evidence` stays in the receipt). After a refusal is final it adds
  an advisory metadata-only model-list check (`reachable`, `model_listed`) that never alters the
  refusal's `reason_code`/receipt (a memory refusal keeps its own numbers as `detail`) and
  swallows its own failures, so the memory-safety reason always stands. Hosted success
  rows carry `elapsed_ms`. `connection_state.reading()` takes the shared configuration
  lock for read-only GETs so concurrent cold-load reads never return `configuration_busy`.
- `operational_check.py` owns the tiny manual inference probe used by stage
  checks, setup validation, and Doctor. Identical routes share one serial probe;
  standalone local waiters get 60 seconds and hosted waiters 30 seconds.
  Verification transports use `SUMMARY_TIMEOUT_SECONDS`, capped by those existing
  probe budgets; ordinary inference keeps its configured timeout. Doctor uses
  its outer bounded waiter and keeps real requests inside callback exclusion. Failures become
  bounded results, not startup errors. Local and explicitly cancellation-owned
  batches stop after a timeout. Ordinary hosted diagnostics may continue to a
  different model; a shared timed-out identity is reused, never automatically retried.
  Cheap `/models` reachability remains the proactive companion. Within one check,
  metadata is requested once per saved provider with serial admission, avoiding
  self-conflict on the nonwaiting configuration lock. Each stage retains its own
  model/capability identity; consent, per-provider timeout and failure states stay
  intact. This is not an inference or model-availability guarantee.
- `probe_failure.py` projects safe typed HTTP/SDK observations: HTTP status (not
  provider error bodies), transport/timeout, empty text, and native identity/terminal
  failure. It reuses HTTPX/SDK status types and the existing request inventory;
  SetupAttempt retains the observation alongside separate completion/HOLD evidence
  instead of replacing it with a generic inventory failure. No prose matching.
- `model_list.py` lists sorted unique model IDs. Remote discovery accepts a
  built-in preset, declared no-auth, non-secret local admission or current exact
  task approval. A custom saved identity alone is not consent; absent/expired
  permission reports needs-approval before lookup. CLI reads only its selected
  persisted config/task receipt, never a template or arbitrary draft. Loopback
  legacy metadata uses only its non-secret local token; native no-auth uses owned
  HTTP without SDK profile construction.
- `presets.py` / `credentials.py` compile eight service cards to
  `ProviderConfig`; only redacted credential metadata crosses setup APIs.

## Verification evidence

Provider capabilities declare adapter paths, not measured model support. Probes
require nonempty output and observed matching model/terminal metadata, never an
exact prompt-response phrase. Setup clients own and close their transport, disable
retries/cache/proxy inheritance/redirects, and reject weights-file URLs. A missing
completion observation cannot pass. Malformed endpoints return failed probe data
without constructing an adapter. Cancellation fences later calls in a dry-run;
its waiter cannot prove remote termination. Local probes refuse UNKNOWN before
construction until an authoritative effective ResourceEnvelope producer exists;
ordinary loopback clients need no key when none is configured; an explicitly
configured credential retains its existing routing behavior. Explicit
verification factory calls enforce admission before any credential lookup
or either adapter, including alternate 127/8 and mapped IPv6 loopbacks. Ordinary
client construction and genuinely remote routes keep their existing behavior.
Cheap listing
continues independently; configured, listed, capable, operational and hardware-safe
are not interchangeable. Actual local-model/hardware acceptance is unmeasured.

## Invariants

- `api_key_env` stores a variable name, never a secret; keys resolve at use time.
- Startup never depends on a provider. Missing endpoints/keys surface only when
  a stage or explicit operational check runs.
- `llm_enabled: false` returns an explicit disabled status without probing a
  provider; runtime AI actions reject with a typed 409 until re-enabled.
- Stages inherit `llm_routing.default` unless they override provider/model.
- `ProviderConfig.temperature` and thinking options are translated in the
  factory; call sites do not build provider-specific wire payloads.
- OpenAI-compatible response reads are bounded by `Settings.summary_timeout_seconds`;
  transport timeout errors propagate to each worker's existing error boundary.

## Checklist engine (`jev_engine.py`)

The one seam that turns Settings → Checklist engine (`checklist_engine` off / local / cloud, `checklist_local_url`) into an
`integrations/jev.Target`: `resolve(config)` for deep reviews, `noul_for(settings)` for Telegram's disagreement check, `check()` for
the Settings Test. Cloud reads its saved access key from the OS keyring (legacy env fallback), is refused under `ZS_OFFLINE`, and
the key is never returned or logged; Local sends no key. No target → callers use the language-model path.
