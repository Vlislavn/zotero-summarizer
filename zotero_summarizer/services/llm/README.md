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

- `factory.py` — `build_client_for_provider(provider, model, *, enable_thinking=None)` /
  `build_client_for_stage(resolved)`. The optional `enable_thinking` forces the reasoning
  flag on/off for THIS client by toggling `chat_template_kwargs.enable_thinking` — a no-op
  for providers that don't already advertise that key (so a plain OpenAI endpoint never gets
  an unknown extra_body key). deep_review uses it to make the DIGEST reason while the trivial
  verification calls stay fast; `runtime.resolve_stage_client(stage, enable_thinking=...)`
  caches the two variants by `(stage, enable_thinking)`.
  Dispatches on `ProviderConfig.type`; `resolve_api_key(provider)` reads the API key from
  the OS keyring, falling back to the env var named by `api_key_env` (reused by
  `model_list`) and raises `APIError(missing_api_key)` when unset. `openai` reuses
  `services._adapters.build_llm` — now threading `provider.temperature` (default 0) and the
  `provider.thinking_effort` translation (via `thinking.py`, applied BEFORE the per-call
  `enable_thinking` override so the digest can still force thinking on); `anthropic` builds
  `integrations.llm_anthropic.AnthropicLLMClient` with a `thinking_budget` from the effort.
- `thinking.py` — pure `thinking_effort` → wire-param translation. `effort_to_anthropic_budget`
  maps a level to an Anthropic thinking budget (off/None → disabled); `apply_effort_openai`
  folds it into `extra_body` by inferred dialect: `chat_template_kwargs` present (vLLM/qwen) →
  `enable_thinking` on/off (graded collapses — documented, not silent); else (plain OpenAI /
  OpenRouter) → top-level `reasoning_effort`. `None` effort is a no-op (back-compat).
- `operational_check.py` — `probe_provider(provider, model)` is the SINGLE shared probe
  mechanism (a tiny prompt → `{status: operational|fail, detail}`); both the per-stage
  `check_stages()` here AND `services/setup/validate.py`'s connection test call it, so
  there is one probe, never two divergent ones. `check_stages()` probes each stage's
  provider and returns per-stage `operational | fail`. Per-stage failures are reported,
  never raised: the app always starts, and the user runs this check manually to verify
  each stage. `check_routing_stages(routing)` exposes that same bounded probe to the
  setup doctor without depending on live global state. Identical provider/model routes share one serial probe; local cold starts get 60s while remote checks retain 8s. Worker calls are bounded so a slow/loading/unreachable provider
  reports `fail: timeout` instead of hanging the button — the check always answers
  within the timeout. Surfaced at `POST /api/admin/llm-check`. `check_reachability()`
  is the CHEAP proactive companion: a per-stage `GET /models` (via `model_list`, no
  tokens, no model load, `_REACH_TIMEOUT_SECS`) returning `reachable` + `base_url`;
  the deep-review surface polls it on mount (`GET /api/admin/llm-reachability`) so a
  dead endpoint shows a banner before a run rather than a silent empty digest.
- `model_list.py` — `list_models_for_provider(provider)` resolves the key
  (`factory.resolve_api_key`) and dispatches by `type` to `integrations.llm_models`,
  returning sorted, unique model ids for the Settings model-picker. Takes the provider
  profile from the request (not saved config), so the user can pick a model before
  saving. Surfaced at `POST /api/admin/llm-models`.
- `presets.py` / `credentials.py` — eight human-facing service cards compile to
  `ProviderConfig`; secrets use the OS keyring with legacy env fallback and only
  redacted presence/source metadata crosses `/api/setup/ai-*`.

## Key invariants

- **Secrets never live in config.** `api_key_env` holds the env-var *name*; the key is
  read here at build time.
- **Startup never depends on a provider.** Clients are built lazily on first use
  (`RuntimeState.resolve_stage_client`) and cached; a missing key/endpoint surfaces only
  when that stage actually runs (or when the operational check is invoked).
- **Inheritance.** A stage with no `provider`/`model` falls back to `llm_routing.default`
  (configure once, override per stage only when needed). See `models/providers.py`.
