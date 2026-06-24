// Settings — thin orchestrator. Owns the runtime-config query, the local form
// state seeded from it, the JSON dirty-check, and the sticky save bar; delegates
// the actual fields to settings/ subcomponents:
//
//   ReadinessStrip     — Zotero·LLM·Goals·Model pills (from useSetupStatus)
//   EssentialsSection  — goals, triage criteria, output language, default
//                        provider, Zotero paths (+ live status / Save paths)
//   AdvancedSection    — <details> with the full LlmRoutingSection (verbatim),
//                        the classifier gate, and the corpus-similarity slider
//   ModelCard / AdminSection — unchanged
//
// The legacy `llm.*` text inputs (draft_model/refine_model/api_base/api_key_env)
// are GONE — they duplicated llm_routing. The transforms live in
// utils/configForm.js (shared with the wizard) and no longer read/write that
// nested block. Backend round-trips the untouched `llm` key from baseConfig.

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchConfig, updateConfig } from '../api/settingsApi.js';
import { ALL_PRIORITIES, configToFormState, formStateToConfig } from '../utils/configForm.js';
import { humanizeError } from '../utils/humanizeError.js';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import AdminSection from '../components/AdminSection.jsx';
import ModelCard from '../components/ModelCard.jsx';
import { Banner } from '../components/form/Fields.jsx';
import Button from '../components/ui/Button.jsx';
import ReadinessStrip from '../components/settings/ReadinessStrip.jsx';
import EssentialsSection from '../components/settings/EssentialsSection.jsx';
import AdvancedSection from '../components/settings/AdvancedSection.jsx';
import UniversityAccessPanel from '../components/settings/UniversityAccessPanel.jsx';

export default function Settings() {
  const queryClient = useQueryClient();
  const { status } = useSetupStatus();

  // React Query owns the canonical server snapshot. The form is local state
  // seeded from `data` on mount and after every successful save.
  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });

  const [form, setForm] = useState(null);
  const [savedBanner, setSavedBanner] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // Zotero paths are written through the dedicated /api/setup/paths route, so
  // they live OUTSIDE the GoalsConfig form (and outside its dirty-check).
  const [pathForm, setPathForm] = useState({ zotero_data_dir: '', pdf_root: '' });

  const seededFormState = useMemo(
    () => configToFormState(configQuery.data),
    [configQuery.data],
  );

  // Cheap dirty-check by JSON-serialised comparison.
  const isDirty = useMemo(() => {
    if (!form || !seededFormState) return false;
    try {
      return JSON.stringify(form) !== JSON.stringify(seededFormState);
    } catch {
      return true;
    }
  }, [form, seededFormState]);

  useEffect(() => {
    if (seededFormState && form === null) {
      setForm(seededFormState);
    }
  }, [seededFormState, form]);

  // Seed the path form once from the resolved status values (don't clobber edits).
  useEffect(() => {
    if (status?.paths) {
      setPathForm((prev) => {
        if (prev.zotero_data_dir || prev.pdf_root) return prev;
        return {
          zotero_data_dir: status.paths.zotero_data_dir?.value || '',
          pdf_root: status.paths.pdf_root?.value || '',
        };
      });
    }
  }, [status?.paths]);

  const saveMutation = useMutation({
    mutationFn: (payload) => updateConfig(payload),
    onSuccess: (resp) => {
      if (resp && resp.config) {
        queryClient.setQueryData(['runtime-config'], resp.config);
        setForm(configToFormState(resp.config));
      } else {
        queryClient.invalidateQueries({ queryKey: ['runtime-config'] });
      }
      queryClient.invalidateQueries({ queryKey: ['setup-status'] });
      setSavedBanner('Saved successfully');
      setTimeout(() => setSavedBanner(''), 3000);
    },
  });

  function updateField(key, value) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function updatePathField(key, value) {
    setPathForm((prev) => ({ ...prev, [key]: value }));
  }

  function toggleDropPriority(priority) {
    setForm((prev) => {
      if (!prev) return prev;
      const set = new Set(prev.gate_drop_priorities || []);
      if (set.has(priority)) set.delete(priority);
      else set.add(priority);
      const next = ALL_PRIORITIES.filter((p) => set.has(p));
      return { ...prev, gate_drop_priorities: next };
    });
  }

  function handleSave(e) {
    e.preventDefault();
    if (!form || !configQuery.data) return;
    setSavedBanner('');
    saveMutation.mutate(formStateToConfig(form, configQuery.data));
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
          Failed to load /api/config: {humanizeError(configQuery.error)}
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

  const saveError = saveMutation.error ? humanizeError(saveMutation.error) : null;
  const saving = saveMutation.isPending;

  return (
    <div className="pb-10 space-y-4">
      <header className="glass rounded-2xl border border-slate-200 p-4">
        <h2 className="text-lg font-bold text-slate-900">Triage Preferences</h2>
        <p className="text-xs text-slate-500 mt-1">
          Edits replace <span className="font-mono">goals.yaml</span> on save.
          Changes apply immediately to in-flight daemon ticks.
        </p>
      </header>

      <ReadinessStrip />

      <form onSubmit={handleSave} className="space-y-4">
        <EssentialsSection
          form={form}
          onUpdate={updateField}
          onOpenAdvanced={() => setAdvancedOpen(true)}
          pathForm={pathForm}
          onUpdatePath={updatePathField}
        />

        <AdvancedSection
          form={form}
          isDirty={isDirty}
          open={advancedOpen}
          onToggle={setAdvancedOpen}
          onUpdate={updateField}
          onToggleDropPriority={toggleDropPriority}
        />

        {/* University-access config — folded into the one form so the single sticky
            Save commits it (the panel keeps only its login action). */}
        <UniversityAccessPanel form={form} onUpdate={updateField} />

        {/* Save bar — sticky-bottom so the action target stays within reach (Fitts's
            Law). ONE status slot: error wins, then unsaved, then saved/idle. */}
        <div className="sticky bottom-0 -mx-4 px-4 py-3 bg-white/95 backdrop-blur border-t border-slate-200 z-10 flex items-center gap-3">
          <Button type="submit" disabled={saving || !isDirty}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
          {/* A way out (Tesler): revert every edit to the last-saved config without a
              page reload. Only offered while dirty — resets the form to its baseline. */}
          {isDirty && !saving && (
            <button
              type="button"
              onClick={() => setForm(seededFormState)}
              className="text-xs px-2.5 py-1 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100"
              title="Discard your unsaved edits and revert to the last-saved settings."
            >
              Discard
            </button>
          )}
          {saveError ? (
            <span className="text-xs text-rose-700">Save failed: {saveError}</span>
          ) : isDirty && !saving ? (
            <span
              className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-900 border border-amber-300"
              title="You have edits that have not been saved yet."
            >
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" aria-hidden />
              Unsaved changes
            </span>
          ) : saving ? null : (
            <span className={`text-xs ${savedBanner ? 'text-emerald-700' : 'text-slate-400'}`}>
              {savedBanner || 'All changes saved.'}
            </span>
          )}
        </div>
      </form>

      {/* Read-only model card + admin actions sit OUTSIDE the config form so their
          action buttons can't be conflated with the config submit. */}
      <ModelCard />

      {/* Admin section lives outside the config form so its action buttons
          (refresh-labels, retrain) can't be conflated with the config submit. */}
      <AdminSection />
    </div>
  );
}
