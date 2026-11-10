// Pure config <-> form-state transforms, shared by BOTH the Settings page and
// the first-run wizard so they emit byte-identical PUT payloads.
//
// Backend contract: zotero_summarizer/models.py :: GoalsConfig.
// Pydantic v2 silently drops unknown top-level keys, so we always round-trip
// the full server payload (`baseConfig`) and only override the editable
// branches before PUT. This guarantees we never lose nested fields the form
// doesn't surface (prompts, relevance_scale, prestige, full_text_refine, …).
//
// NOTE: the legacy `llm:` block (draft_model/refine_model/api_base/api_key_env)
// is NO LONGER UI-editable — it duplicated the `llm_routing` editor. The
// backend still auto-migrates it and the full `baseConfig` is round-tripped, so
// the nested `llm` key is preserved untouched; we simply never read from it
// into the form nor write it back from the form.

// Resolve a stage's effective provider+model on the client, mirroring the
// backend resolve_stage (zotero_summarizer/models/providers.py): an unset stage
// field inherits the global `default`. Lets ActiveModelsSummary answer "what's
// running for this stage right now" instantly from the loaded config — no probe.
export function resolveStage(routing, stage) {
  const r = routing || {};
  const sel = r[stage] || {};
  const def = r.default || {};
  const providerName = sel.provider || def.provider || null;
  const model = sel.model || def.model || null;
  const provider = (r.providers || []).find((p) => p?.name === providerName) || null;
  return { stage, providerName, model, provider, inherits: !sel.provider && !sel.model };
}

export function isLocalProvider(provider) {
  if (!provider?.base_url) return false;
  try {
    const host = new URL(provider.base_url).hostname.replace(/^\[|\]$/g, '').toLowerCase().replace(/\.$/, '');
    return ['localhost', '0.0.0.0', '::', '::1', '::ffff:0:0'].includes(host)
      || host.endsWith('.localhost') || /^127(?:\.\d{1,3}){3}$/.test(host)
      || /^::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}$/.test(host);
  } catch { return false; }
}

export function localProfileForStage(routing, catalog, stage = 'default') {
  const { provider, model } = resolveStage(routing, stage);
  if (!isLocalProvider(provider)) return null;
  return catalog?.profiles?.find((profile) => profile.model && profile.model === model)
    || catalog?.profiles?.find((profile) => profile.id === 'existing') || null;
}

export function splitLines(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export function joinLines(items) {
  if (!Array.isArray(items)) return '';
  return items.join('\n');
}

// Display-only tidy of a goal line: stray wrapping quotes / trailing commas left by
// hand-edited YAML lists. The stored value is untouched until the user saves.
export function cleanGoal(line) {
  let text = String(line).trim().replace(/[,;\s]+$/, '');
  if (text.length > 1 && /^["'“”].*["'“”]$/.test(text)) text = text.slice(1, -1).trim().replace(/[,;\s]+$/, '');
  return text;
}

// Background-processing knobs: [section, key, default, min, max] — ranges mirror
// QualityReviewConfig / FeedsConfig in models/ (the backend rejects anything else).
export const PROCESSING_KNOBS = {
  fleet_papers: ['quality_review', 'fleet_papers', 8, 0, 16],
  calls_per_paper: ['quality_review', 'calls_per_paper', 2, 0, 16],
  backfill_days: ['feeds', 'backfill_days', 7, 1, 60],
  max_pages_per_feed: ['feeds', 'max_pages_per_feed', 10, 1, 50],
};
export const processingKnobValid = (key, value) => Number.isInteger(value) && value >= PROCESSING_KNOBS[key][3] && value <= PROCESSING_KNOBS[key][4];

// Convert the GoalsConfig server payload into the flat shape backing the form.
// Nested objects (corpus, classifier_gate, …) get pulled apart so each input
// owns a single string/number/bool.
export function configToFormState(cfg) {
  if (!cfg) return null;
  const ua = cfg.university_access || {};
  return {
    llm_enabled: cfg.llm_enabled !== false,
    research_goals_text: joinLines(Array.isArray(cfg.research_goals) ? cfg.research_goals.map(cleanGoal) : cfg.research_goals),
    triage_criteria_text: joinLines(cfg.triage_criteria),
    output_language: cfg.output_language || 'English',
    review_depth: cfg.review_depth === 'fast' ? 'fast' : 'thorough',
    checklist_engine: ['local', 'cloud'].includes(cfg.checklist_engine) ? cfg.checklist_engine : 'off',
    checklist_local_url: cfg.checklist_local_url || 'http://127.0.0.1:8766/v1/systemone',
    ...Object.fromEntries(Object.entries(PROCESSING_KNOBS).map(([key, [section, name, fallback]]) => [key, cfg[section]?.[name] ?? fallback])),
    // University access (browser PDF fetch) — folded into the one config form so
    // the single sticky Save commits it; the panel keeps only its login action.
    ua_enabled: Boolean(ua.enabled),
    ua_login_url: ua.login_url || '',
    ua_ezproxy_prefix: ua.ezproxy_prefix || '',
    ua_cookie_browser: ua.cookie_browser || '',
    // Per-stage LLM routing stays a structured object (not flattened like the
    // other fields) — the connection task edits it as a tree, and the
    // JSON.stringify dirty-check + formStateToConfig both handle nesting fine.
    // The backend always synthesizes `llm_routing` for GET, but guard with a
    // sensible empty shape so the editor never crashes on a malformed payload.
    llm_routing: cfg.llm_routing
      ? JSON.parse(JSON.stringify(cfg.llm_routing))
      : {
          providers: [],
          default: { provider: null, model: null },
          feed: { provider: null, model: null },
          backlog: { provider: null, model: null },
          deep_review: { provider: null, model: null },
        },
  };
}

// Advance only the connection-owned branch; preserve actual drafts, adopt clean
// unrelated fields from the fresh saved snapshot instead of inventing new edits.
export function rebaseConnectionForm(form, previousConfig, nextConfig) {
  const previous = configToFormState(previousConfig);
  const next = configToFormState(nextConfig);
  if (!form || !previous || !next) return next;
  return Object.fromEntries(Object.keys(next).map(key => [key,
    key === 'llm_routing' || JSON.stringify(form[key]) === JSON.stringify(previous[key]) ? next[key] : form[key],
  ]));
}

export function scopedFormStateToConfig(form, baseConfig, ownedKeys) {
  const projected = formStateToConfig(form, baseConfig);
  return { ...baseConfig, ...Object.fromEntries(ownedKeys.map(key => [key, projected[key]])) };
}

// Merge form edits back onto the full server payload. Deep-clone first so
// we never mutate the React Query cache entry in place.
export function formStateToConfig(form, baseConfig) {
  const next = JSON.parse(JSON.stringify(baseConfig || {}));
  next.research_goals = splitLines(form.research_goals_text);
  next.triage_criteria = splitLines(form.triage_criteria_text);
  next.output_language = form.output_language || 'English';
  next.llm_enabled = form.llm_enabled !== false;
  next.review_depth = form.review_depth === 'fast' ? 'fast' : 'thorough';
  next.checklist_engine = ['local', 'cloud'].includes(form.checklist_engine) ? form.checklist_engine : 'off';
  next.checklist_local_url = (form.checklist_local_url || '').trim() || 'http://127.0.0.1:8766/v1/systemone';
  for (const [key, [section, name]] of Object.entries(PROCESSING_KNOBS)) {
    if (form[key] !== undefined) next[section] = { ...(next[section] || {}), [name]: form[key] };
  }
  // NOTE: deliberately NO `next.llm`, `next.corpus`, or `next.classifier_gate`
  // write — those are system-owned knobs (validated code defaults, ZS_* env, and
  // per-user calibration). Like `prestige`, they round-trip from baseConfig
  // untouched and are no longer UI-editable. The backend persists only the
  // user-owned keys (services/_common.write_user_config).
  next.university_access = {
    ...(next.university_access || {}),
    enabled: Boolean(form.ua_enabled),
    login_url: form.ua_login_url || '',
    ezproxy_prefix: form.ua_ezproxy_prefix || '',
    cookie_browser: form.ua_cookie_browser || '',
  };
  // Write the structured routing tree straight back. The backend re-validates
  // it strictly (unique provider names, openai requires base_url, env-var name
  // not secret, stage providers must reference an existing provider, …) and
  // surfaces failures via the 400 body's `.message`, which `request()` turns
  // into the save-error banner. `next` is already a deep clone of baseConfig.
  if (form.llm_routing !== undefined) {
    next.llm_routing = form.llm_routing;
  }
  return next;
}
