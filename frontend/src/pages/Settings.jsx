import { useEffect, useMemo, useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { fetchConfig, updateConfig } from '../api/settingsApi.js';
import AdminSection from '../components/AdminSection.jsx';
import ModelCard from '../components/ModelCard.jsx';

// Ported from the legacy Alpine settings tab in zotero_summarizer/web/ui.html
// (search for x-show="activeTab === 'settings'", line ~1592).
//
// Backend contract: zotero_summarizer/models.py :: GoalsConfig.
// Pydantic v2 silently drops unknown top-level keys, so we always round-trip
// the full server payload (`baseConfig`) and only override the editable
// branches before PUT. This guarantees we never lose nested fields the form
// doesn't surface (prompts, relevance_scale, prestige, full_text_refine, …).

const ALL_PRIORITIES = ['must_read', 'should_read', 'could_read', 'dont_read'];
const CLASSIFIER_MODEL_OPTIONS = ['tabpfn', 'lightgbm', 'logreg'];

function splitLines(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function joinLines(items) {
  if (!Array.isArray(items)) return '';
  return items.join('\n');
}

// Convert the GoalsConfig server payload into the flat shape backing the form.
// Nested objects (corpus, llm, classifier_gate, …) get pulled apart so each
// input owns a single string/number/bool — this matches AnnotationVerdict's
// "small form state, mirror server on PUT" pattern.
function configToFormState(cfg) {
  if (!cfg) return null;
  const llm = cfg.llm || {};
  const corpus = cfg.corpus || {};
  const gate = cfg.classifier_gate || {};
  return {
    research_goals_text: joinLines(cfg.research_goals),
    triage_criteria_text: joinLines(cfg.triage_criteria),
    output_language: cfg.output_language || 'English',
    llm_draft_model: llm.draft_model || '',
    llm_refine_model: llm.refine_model || '',
    llm_api_base: llm.api_base || '',
    llm_api_key_env: llm.api_key_env || '',
    corpus_similarity_threshold: Number(corpus.similarity_threshold ?? -0.3),
    gate_enabled: Boolean(gate.enabled),
    gate_model_name: gate.model_name || 'tabpfn',
    gate_drop_priorities: Array.isArray(gate.drop_priorities)
      ? [...gate.drop_priorities]
      : ['dont_read'],
    gate_raw_score_dont_read_below: Number(gate.raw_score_dont_read_below ?? 0),
    gate_audit_sample_per_tick: Number(gate.audit_sample_per_tick ?? 0),
  };
}

// Merge form edits back onto the full server payload. Deep-clone first so
// we never mutate the React Query cache entry in place.
function formStateToConfig(form, baseConfig) {
  const next = JSON.parse(JSON.stringify(baseConfig || {}));
  next.research_goals = splitLines(form.research_goals_text);
  next.triage_criteria = splitLines(form.triage_criteria_text);
  next.output_language = form.output_language || 'English';
  next.llm = {
    ...(next.llm || {}),
    draft_model: form.llm_draft_model,
    refine_model: form.llm_refine_model,
    api_base: form.llm_api_base,
    api_key_env: form.llm_api_key_env,
  };
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
  return next;
}

function SectionCard({ title, description, children }) {
  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500">
        {title}
      </h3>
      {description && (
        <p className="text-xs text-slate-500 mt-1 mb-3">{description}</p>
      )}
      <div className={description ? '' : 'mt-3'}>{children}</div>
    </div>
  );
}

// One generic field component covers text / number / textarea / select. Cuts
// the per-input boilerplate vs. shipping five near-identical wrappers and
// keeps the file under the 500-LOC budget.
const INPUT_CLS =
  'w-full mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500';

function Field({ label, kind = 'text', value, onChange, hint, options, rows = 8, step = 0.01, min, max }) {
  let control;
  if (kind === 'textarea') {
    control = (
      <textarea
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        className={`${INPUT_CLS} font-mono`}
      />
    );
  } else if (kind === 'select') {
    control = (
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className={`${INPUT_CLS} bg-white`}
      >
        {(options || []).map((opt) => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    );
  } else if (kind === 'number') {
    control = (
      <input
        type="number"
        value={value ?? 0}
        step={step}
        min={min}
        max={max}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
        className={INPUT_CLS}
      />
    );
  } else {
    control = (
      <input
        type="text"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className={INPUT_CLS}
      />
    );
  }
  return (
    <label className="block">
      <span className="text-sm font-semibold text-slate-700">{label}</span>
      {control}
      {hint && <span className="text-xs text-slate-500 mt-1 block">{hint}</span>}
    </label>
  );
}

function CheckboxField({ label, checked, onChange, hint }) {
  return (
    <label className="flex items-start gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={Boolean(checked)}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 rounded"
      />
      <span>
        <span className="text-sm font-semibold text-slate-700">{label}</span>
        {hint && (
          <span className="text-xs text-slate-500 mt-0.5 block">{hint}</span>
        )}
      </span>
    </label>
  );
}

function Banner({ kind, children }) {
  if (!children) return null;
  const cls =
    kind === 'error'
      ? 'bg-rose-50 border-rose-200 text-rose-800'
      : 'bg-emerald-50 border-emerald-200 text-emerald-900';
  return (
    <div
      role="status"
      aria-live="polite"
      className={`px-3 py-2 rounded-lg border text-sm ${cls}`}
    >
      {children}
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();

  // React Query owns the canonical server snapshot. The form is local state
  // seeded from `data` on mount and after every successful save (so the
  // dropped/unknown fields stay in sync if the backend rewrote them).
  const configQuery = useQuery({
    queryKey: ['runtime-config'],
    queryFn: fetchConfig,
  });

  const [form, setForm] = useState(null);
  const [savedBanner, setSavedBanner] = useState('');

  // Seed the form once data lands. Use a memoized form-state shape so we
  // only re-seed when the server payload identity actually changes.
  const seededFormState = useMemo(
    () => configToFormState(configQuery.data),
    [configQuery.data],
  );

  // Cheap dirty-check by JSON-serialised comparison. Both shapes are
  // small flat dicts with primitive leaves; stable key order is OK.
  const isDirty = useMemo(() => {
    if (!form || !seededFormState) return false;
    try {
      return JSON.stringify(form) !== JSON.stringify(seededFormState);
    } catch {
      // JSON.stringify on plain config dicts cannot fail unless someone
      // wires in a circular structure later; treat that as dirty so the
      // user can hit Save and surface the issue rather than silently
      // marking the form clean.
      return true;
    }
  }, [form, seededFormState]);

  useEffect(() => {
    if (seededFormState && form === null) {
      setForm(seededFormState);
    }
  }, [seededFormState, form]);

  const saveMutation = useMutation({
    mutationFn: (payload) => updateConfig(payload),
    onSuccess: (resp) => {
      // Backend returns { status, config }. Update React Query cache so the
      // form re-seeds against the authoritative server-validated snapshot.
      if (resp && resp.config) {
        queryClient.setQueryData(['runtime-config'], resp.config);
        setForm(configToFormState(resp.config));
      } else {
        queryClient.invalidateQueries({ queryKey: ['runtime-config'] });
      }
      setSavedBanner('Saved successfully');
      // Doherty-threshold style: clear the banner after 3s so it doesn't
      // become permanent visual noise (matches the legacy Alpine UX).
      setTimeout(() => setSavedBanner(''), 3000);
    },
  });

  function updateField(key, value) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function toggleDropPriority(priority) {
    setForm((prev) => {
      if (!prev) return prev;
      const set = new Set(prev.gate_drop_priorities || []);
      if (set.has(priority)) set.delete(priority);
      else set.add(priority);
      // Preserve canonical order so the YAML diff stays stable.
      const next = ALL_PRIORITIES.filter((p) => set.has(p));
      return { ...prev, gate_drop_priorities: next };
    });
  }

  function handleSave(e) {
    e.preventDefault();
    if (!form || !configQuery.data) return;
    setSavedBanner('');
    const payload = formStateToConfig(form, configQuery.data);
    saveMutation.mutate(payload);
  }

  if (configQuery.isLoading) {
    return (
      <div className="glass rounded-2xl border border-slate-200 p-4 text-sm text-slate-500">
        Loading settings…
      </div>
    );
  }

  if (configQuery.isError) {
    return (
      <div className="space-y-3">
        <Banner kind="error">
          Failed to load /api/config:{' '}
          {configQuery.error?.message || String(configQuery.error)}
        </Banner>
        <button
          type="button"
          onClick={() => configQuery.refetch()}
          className="px-3 py-1.5 rounded-lg border border-slate-300 text-sm hover:bg-slate-100"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!form) {
    return (
      <div className="glass rounded-2xl border border-slate-200 p-4 text-sm text-slate-500">
        Preparing form…
      </div>
    );
  }

  const saveError = saveMutation.error?.message || null;
  const saving = saveMutation.isPending;

  return (
    <div className="pb-10">
      <form onSubmit={handleSave} className="space-y-4">
        <header className="glass rounded-2xl border border-slate-200 p-4">
          <h2 className="text-lg font-bold text-slate-900">Triage Preferences</h2>
          <p className="text-xs text-slate-500 mt-1">
            Edits replace <span className="font-mono">goals.yaml</span> on save.
            Changes apply immediately to in-flight daemon ticks.
          </p>
        </header>

      {(savedBanner || saveError) && (
        <Banner kind={saveError ? 'error' : 'success'}>
          {saveError ? `Save failed: ${saveError}` : savedBanner}
        </Banner>
      )}

      {/* ---------- Research goals ---------- */}
      <SectionCard
        title="Research goals"
        description="Free-text descriptions of what you're researching. The triage prompt uses these for goal_alignment scoring — one goal per line."
      >
        <Field
          kind="textarea"
          label="Goals (one per line)"
          value={form.research_goals_text}
          onChange={(v) => updateField('research_goals_text', v)}
          rows={8}
        />
      </SectionCard>

      {/* ---------- Triage criteria ---------- */}
      <SectionCard
        title="Triage criteria"
        description="Per-paper acceptance criteria + the LLM endpoint that scores them."
      >
        <div className="space-y-4">
          <Field
            kind="textarea"
            label="Triage criteria (one per line)"
            value={form.triage_criteria_text}
            onChange={(v) => updateField('triage_criteria_text', v)}
            rows={6}
            hint="Each line is a hard or soft criterion the LLM weighs when assigning the relevance score."
          />
          <div className="grid md:grid-cols-2 gap-4">
            <Field
              label="Draft model"
              value={form.llm_draft_model}
              onChange={(v) => updateField('llm_draft_model', v)}
              hint="Fast model used for the map step (chunk summaries)."
            />
            <Field
              label="Refine model"
              value={form.llm_refine_model}
              onChange={(v) => updateField('llm_refine_model', v)}
              hint="Stronger model used for the final triage + refine step."
            />
            <Field
              label="API base URL"
              value={form.llm_api_base}
              onChange={(v) => updateField('llm_api_base', v)}
              hint="OpenAI-compatible endpoint (e.g. http://localhost:11434/v1 for Ollama)."
            />
            <Field
              label="API key env var"
              value={form.llm_api_key_env}
              onChange={(v) => updateField('llm_api_key_env', v)}
              hint="Name of the environment variable holding the API key. Must be set in the daemon's env."
            />
            <Field
              label="Output language"
              value={form.output_language}
              onChange={(v) => updateField('output_language', v)}
            />
            <label className="block">
              <span className="text-sm font-semibold text-slate-700">
                Corpus similarity threshold
              </span>
              <input
                type="range"
                min="-1"
                max="1"
                step="0.01"
                value={form.corpus_similarity_threshold ?? -0.3}
                onChange={(e) =>
                  updateField('corpus_similarity_threshold', Number(e.target.value))
                }
                className="w-full mt-1"
              />
              <div className="text-xs text-slate-600 font-mono">
                {Number(form.corpus_similarity_threshold ?? 0).toFixed(2)}
              </div>
              <span className="text-xs text-slate-500 mt-1 block">
                Cosine similarity floor for goal-alignment retrieval. Lower =
                more permissive matching.
              </span>
            </label>
          </div>
        </div>
      </SectionCard>

      {/* ---------- Classifier gate ---------- */}
      <SectionCard
        title="Classifier gate"
        description="Optional fast-reject layer. When enabled, the daemon trains a small classifier from the golden CSV and drops items in the configured priorities before they ever reach the LLM."
      >
        <div className="space-y-4">
          <CheckboxField
            label="Enable classifier gate"
            checked={form.gate_enabled}
            onChange={(v) => updateField('gate_enabled', v)}
            hint="Off keeps every dedup'd feed item flowing into the LLM (slower, more accurate)."
          />
          <div className="grid md:grid-cols-2 gap-4">
            <Field
              kind="select"
              label="Classifier model"
              value={form.gate_model_name}
              onChange={(v) => updateField('gate_model_name', v)}
              options={CLASSIFIER_MODEL_OPTIONS}
              hint="tabpfn = best F1, slower. lightgbm = fast. logreg = baseline."
            />
            <Field
              kind="number"
              label="Raw-score dont_read floor"
              value={form.gate_raw_score_dont_read_below}
              onChange={(v) => updateField('gate_raw_score_dont_read_below', v)}
              step={0.01}
              min={0}
              max={1}
              hint="Items with raw classifier prob < this cutoff get forced to dont_read. 0 disables."
            />
            <Field
              kind="number"
              label="Audit sample / tick"
              value={form.gate_audit_sample_per_tick}
              onChange={(v) => updateField('gate_audit_sample_per_tick', v)}
              step={1}
              min={0}
              max={20}
              hint="Counterfactual audit: resurrect N gate-rejected rows each tick so the user's verdict on them estimates false-negative rate. 0 disables."
            />
          </div>
          <fieldset>
            <legend className="text-sm font-semibold text-slate-700 mb-2">Drop priorities</legend>
            <p className="text-xs text-slate-500 mb-2">
              Priorities the gate short-circuits. Items predicted into any checked bucket skip the LLM entirely.
            </p>
            <div className="flex flex-wrap gap-3">
              {ALL_PRIORITIES.map((priority) => (
                <label key={priority} className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={(form.gate_drop_priorities || []).includes(priority)}
                    onChange={() => toggleDropPriority(priority)}
                    className="rounded"
                  />
                  <span className="font-mono">{priority}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      </SectionCard>

      {/* Save bar — sticky-bottom so the action target stays within reach (Fitts's Law). */}
        <div className="sticky bottom-0 -mx-4 px-4 py-3 bg-white/95 backdrop-blur border-t border-slate-200 z-10 flex items-center gap-3">
          <button
            type="submit"
            disabled={saving || !isDirty}
            className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          {isDirty && !saving && (
            <span
              className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-900 border border-amber-300"
              title="You have edits that have not been saved yet."
            >
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" aria-hidden />
              Unsaved changes
            </span>
          )}
          {!isDirty && !saving && !savedBanner && !saveError && (
            <span className="text-xs text-slate-400">All changes saved.</span>
          )}
          {saving && <span className="text-xs text-slate-500">PUT /api/config in flight…</span>}
          {!saving && savedBanner && <span className="text-xs text-emerald-700">{savedBanner}</span>}
          {!saving && saveError && <span className="text-xs text-rose-700">{saveError}</span>}
        </div>
      </form>

      {/* Read-only model card sits above the admin actions so the user
          sees what's currently deployed before retraining or refreshing. */}
      <ModelCard />

      {/* Admin section lives outside the config form so its action buttons
          (refresh-labels, retrain) can't be conflated with the config submit. */}
      <AdminSection />
    </div>
  );
}
