# services/llm — provider-aware client construction

`goals.yaml:llm_routing` resolves each stage to a provider/model; the runtime
builds and caches an `LLMClient` lazily.

```text
llm_routing → resolve_stage → ResolvedStage → factory → LLMClient
                feed:text      feed · backlog · deep_review
```

## Files

- `factory.py` rechecks retained capability requirements before credentials or
  transport, dispatches OpenAI-compatible/native Anthropic clients and resolves
  secrets from the keyring with env fallback. OpenAI-compatible transports receive
  the process `SUMMARY_TIMEOUT_SECONDS` request deadline; its optional
  `enable_thinking` overrides only clients already advertising that capability.
  Declared `structured_output` applies schema constraints to every Pydantic call
  through the shared adapter, including CLI calls and verifier/correction passes.
  Explicit local setup observation reuses this builder with per-request inline
  receipts, SDK retries/cache off and a scoped no-redirect/no-env-proxy HTTP client;
  observer injection is refused for hosted providers. Defaults for ordinary and
  hosted clients are unchanged; these receipts are not memory-envelope telemetry.
- `thinking.py` translates effort to Anthropic budgets, OpenAI
  `reasoning_effort`, or qwen/vLLM `enable_thinking`. Explicit `off` sends
  `reasoning_effort: none` in the plain OpenAI dialect; omitting it would preserve
  the server's potentially enabled reasoning default. Unset effort remains a no-op.
- `operational_check.py` owns the tiny manual inference probe used by stage
  checks, setup validation, and Doctor. A typed incompatible selection becomes an
  existing failed/unreachable diagnostic with required/declared capability names,
  without credentials, client construction or a probe for that stage. Unrelated
  resolver errors propagate; deliberately disabled AI remains disabled. Identical
  routes share one serial probe; local waiters get 60 seconds and hosted waiters 30 seconds; these do not terminate
  callbacks. Setup injects per-callback admission/abandonment while retaining the
  unique identity cache. Observed probes require nonempty text and close their
  owned connection transport in `finally` before the callback releases its lock.
  This cleanup does not terminate a model or establish server-wide idleness.
  Default non-setup behavior and cheap `/models` remain.
- `model_list.py` lists sorted unique model IDs. Remote discovery accepts a
  built-in preset or an identity already present in the live saved config; a
  custom remote draft must be saved first. Loopback listing receives only a
  non-secret local token, so a request cannot pair an arbitrary destination
  with a server-held credential.
- `presets.py` / `credentials.py` compile eight service cards to
  `ProviderConfig`; only redacted credential metadata crosses setup APIs.

## Declared admission (not capability measurement)

The existing configured feed summary call resolves `feed` through `RuntimeState`;
`resolve_stage` admits that route only when text is available. Requirements intersect
an explicit profile declaration with the enabled adapter. There is no provider/model
fallback, probing, new stage or inventory. Config-save cache invalidation is unchanged.
Backlog, deep review and explicitly supplied clients are not migrated here.

A public configuration example (connection values are placeholders):

```yaml
llm_routing:
  providers:
    - name: summaries
      type: openai
      base_url: https://llm.example/v1
      api_key_env: SUMMARY_API_KEY
      capabilities: [text]
  default:
    provider: summaries
    model: your-model-id
```

Only `text` and `native_json_schema` are admitted capability names. Omitted/null
`capabilities` preserves legacy adapter-known text and legacy serialized identity;
`[]` explicitly lacks text and fails feed selection. Native schema availability
needs both an explicit declaration and the existing OpenAI `structured_output: true`
option or native Anthropic parsing. Declarations never inject schema/LLM kwargs or
change budgets, concurrency, credentials or reasoning controls. Prompted JSON and
Pydantic validation alone are not native schema support. These declarations do not
measure provider support/context capacity, prove setup Ready or grade paper quality.

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
