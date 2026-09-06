# services/llm — provider-aware client construction

`goals.yaml:llm_routing` resolves each stage to a provider/model; the runtime
builds and caches an `LLMClient` lazily.

```text
llm_routing → resolve_stage → ResolvedStage → factory → LLMClient
                              feed · backlog · deep_review
```

## Files

- `factory.py` dispatches OpenAI-compatible/native Anthropic clients and resolves
  secrets from the keyring with env fallback. OpenAI-compatible transports receive
  the process `SUMMARY_TIMEOUT_SECONDS` request deadline; its optional
  `enable_thinking` overrides only clients already advertising that capability.
- `thinking.py` translates effort to Anthropic budgets, OpenAI
  `reasoning_effort`, or qwen/vLLM `enable_thinking`.
- `operational_check.py` owns the tiny manual inference probe used by stage
  checks, setup validation, and Doctor. Identical routes share one serial probe;
  local cold starts get 60 seconds and hosted checks 30 seconds. Failures become
  bounded results, not startup errors. Cheap `/models` reachability remains the
  proactive companion.
- `model_list.py` lists sorted unique model IDs. Remote discovery accepts a
  built-in preset or an identity already present in the live saved config; a
  custom remote draft must be saved first. Loopback listing receives only a
  non-secret local token, so a request cannot pair an arbitrary destination
  with a server-held credential.
- `presets.py` / `credentials.py` compile eight service cards to
  `ProviderConfig`; only redacted credential metadata crosses setup APIs.

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
