# services/llm — provider-aware LLM client construction

Resolves the per-stage provider+model routing (`goals.yaml: llm_routing`) into
live LLM clients, and runs the manual "is it operational" probe.

```
 goals.yaml: llm_routing                     RuntimeState.resolve_stage_client(stage)
 ┌───────────────────────────┐                         │
 │ providers: [ {name,type,   │   resolve_stage()       ▼
 │   base_url,api_key_env,..} ]│ ───────────────▶ ResolvedStage(provider, model)
 │ default: {provider,model}  │                         │
 │ feed:   {provider?,model?} │                         ▼  factory.build_client_for_stage
 │ backlog:{provider?,model?} │            ┌──────────────────────────────┐
 │ deep_review:{...}          │            │ type=openai   → build_llm()   │ (OnPrem OpenAI-compat)
 └───────────────────────────┘            │ type=anthropic→ AnthropicLLM  │ (native messages API)
                                           └──────────────────────────────┘
                                                         │  both satisfy LLMClient
                                                         ▼  .prompt / .pydantic_prompt
                          feed triage · backlog drain · deep review call sites
```

## Files

- `factory.py` — `build_client_for_provider(provider, model)` / `build_client_for_stage(resolved)`.
  Dispatches on `ProviderConfig.type`; `resolve_api_key(provider)` reads the API key from
  the env var named by `api_key_env` (the single key-resolution point, reused by
  `model_list`) and raises `APIError(missing_api_key)` when unset. `openai` reuses
  `services._adapters.build_llm`; `anthropic` builds `integrations.llm_anthropic.AnthropicLLMClient`.
- `operational_check.py` — `check_stages()` probes each stage's provider with a tiny
  prompt and returns per-stage `operational | fail`. Per-stage failures are reported,
  never raised: the app always starts, and the user runs this check manually to verify
  each stage. Probes run in worker threads (`asyncio.to_thread`), each bounded by a
  per-stage timeout (`_PROBE_TIMEOUT_SECS`) so a slow/loading/unreachable provider
  reports `fail: timeout` instead of hanging the button — the check always answers
  within the timeout. Surfaced at `POST /api/admin/llm-check`.
- `model_list.py` — `list_models_for_provider(provider)` resolves the key
  (`factory.resolve_api_key`) and dispatches by `type` to `integrations.llm_models`,
  returning sorted, unique model ids for the Settings model-picker. Takes the provider
  profile from the request (not saved config), so the user can pick a model before
  saving. Surfaced at `POST /api/admin/llm-models`.

## Key invariants

- **Secrets never live in config.** `api_key_env` holds the env-var *name*; the key is
  read here at build time.
- **Startup never depends on a provider.** Clients are built lazily on first use
  (`RuntimeState.resolve_stage_client`) and cached; a missing key/endpoint surfaces only
  when that stage actually runs (or when the operational check is invoked).
- **Inheritance.** A stage with no `provider`/`model` falls back to `llm_routing.default`
  (configure once, override per stage only when needed). See `models/providers.py`.
