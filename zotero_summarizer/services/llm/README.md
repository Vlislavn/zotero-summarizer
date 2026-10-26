# services/llm — provider-aware client construction

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
not hardware admission. Local UNKNOWN still refuses before construction, ordinary
hosted diagnostics keep independent-model continuation, and client close is cleanup
rather than finality. Safety receipts stay in the existing selected Doctor file.

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
  A verification/light-call override lowers an active plain low/medium/high
  `reasoning_effort` to `low`: reasoning-native models need not support `none`.
  Explicit configured off stays off; chat-template thinking can be disabled.
  No unsupported parameter is injected and generator configuration is unchanged.
  Declared `structured_output` applies schema constraints to every Pydantic call
  through the shared adapter, including CLI calls and verifier/correction passes.
- `thinking.py` translates effort to Anthropic budgets, OpenAI
  `reasoning_effort`, or qwen/vLLM `enable_thinking`. Explicit `off` sends
  `reasoning_effort: none` in the plain OpenAI dialect; omitting it would preserve
  the server's potentially enabled reasoning default. Unset effort remains a no-op.
- `operational_check.py` owns the tiny manual inference probe used by stage
  checks, setup validation, and Doctor. Identical routes share one serial probe;
  standalone local checks get 60 seconds and hosted checks 30 seconds. Doctor uses
  its outer bounded waiter and keeps real requests inside callback exclusion. Failures become
  bounded results, not startup errors. Local and explicitly cancellation-owned
  batches stop after a timeout. Ordinary hosted diagnostics may continue to a
  different model; a shared timed-out identity is reused, never automatically retried.
  Cheap `/models` reachability remains the proactive companion.
- `model_list.py` lists sorted unique model IDs. Remote discovery accepts a
  built-in preset or an identity already present in the live saved config. With
  no live config holder (CLI Doctor), it reads only the selected persisted config,
  never a default template or a draft. A custom remote draft must be saved first. Loopback listing receives only a
  non-secret local token, so a request cannot pair an arbitrary destination
  with a server-held credential.
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
