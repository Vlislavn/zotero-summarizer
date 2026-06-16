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

const ALL_PRIORITIES = ['must_read', 'should_read', 'could_read', 'dont_read'];

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

// Convert the GoalsConfig server payload into the flat shape backing the form.
// Nested objects (corpus, classifier_gate, …) get pulled apart so each input
// owns a single string/number/bool.
export function configToFormState(cfg) {
  if (!cfg) return null;
  const corpus = cfg.corpus || {};
  const gate = cfg.classifier_gate || {};
  return {
    research_goals_text: joinLines(cfg.research_goals),
    triage_criteria_text: joinLines(cfg.triage_criteria),
    output_language: cfg.output_language || 'English',
    corpus_similarity_threshold: Number(corpus.similarity_threshold ?? -0.3),
    gate_enabled: Boolean(gate.enabled),
    gate_model_name: gate.model_name || 'tabpfn',
    gate_drop_priorities: Array.isArray(gate.drop_priorities)
      ? [...gate.drop_priorities]
      : ['dont_read'],
    gate_raw_score_dont_read_below: Number(gate.raw_score_dont_read_below ?? 0),
    gate_audit_sample_per_tick: Number(gate.audit_sample_per_tick ?? 0),
    // Per-stage LLM routing stays a structured object (not flattened like the
    // other fields) — LlmRoutingSection edits it as a tree, and the
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

// Merge form edits back onto the full server payload. Deep-clone first so
// we never mutate the React Query cache entry in place.
export function formStateToConfig(form, baseConfig) {
  const next = JSON.parse(JSON.stringify(baseConfig || {}));
  next.research_goals = splitLines(form.research_goals_text);
  next.triage_criteria = splitLines(form.triage_criteria_text);
  next.output_language = form.output_language || 'English';
  // NOTE: deliberately NO `next.llm = {...}` write — the legacy block is
  // round-tripped from baseConfig untouched and is no longer UI-editable.
  next.corpus = {
    ...(next.corpus || {}),
    similarity_threshold: Number(form.corpus_similarity_threshold ?? -0.3),
  };
  next.classifier_gate = {
    ...(next.classifier_gate || {}),
    enabled: Boolean(form.gate_enabled),
    model_name: form.gate_model_name,
    drop_priorities: Array.isArray(form.gate_drop_priorities)
      ? [...form.gate_drop_priorities]
      : [],
    raw_score_dont_read_below: Number(form.gate_raw_score_dont_read_below ?? 0),
    audit_sample_per_tick: Number(form.gate_audit_sample_per_tick ?? 0),
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

export { ALL_PRIORITIES };
