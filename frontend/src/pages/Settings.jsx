// Basic Settings owns AI, research intent, Zotero and sources; operational
// controls are disclosed separately and unsurfaced config round-trips unchanged.

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchConfig, updateConfig } from '../api/settingsApi.js';
import { configToFormState, formStateToConfig } from '../utils/configForm.js';
import { humanizeError } from '../utils/humanizeError.js';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import { Banner } from '../components/form/Fields.jsx';
import Button from '../components/ui/Button.jsx';
import ReadinessStrip from '../components/settings/ReadinessStrip.jsx';
import AiModelsSection from '../components/settings/AiModelsSection.jsx';
import EssentialsSection from '../components/settings/EssentialsSection.jsx';
import UniversityAccessPanel from '../components/settings/UniversityAccessPanel.jsx';
import CalibrationCard from '../components/settings/CalibrationCard.jsx';
import DeploymentCard from '../components/settings/DeploymentCard.jsx';
import RssFeedsSection from '../components/settings/RssFeedsSection.jsx';

export default function Settings() {
  const queryClient = useQueryClient();
  const { status } = useSetupStatus();

  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });

  const [form, setForm] = useState(null);
  const [savedBanner, setSavedBanner] = useState('');
  const [modelsOpen, setModelsOpen] = useState(false);
  const [pathForm, setPathForm] = useState({ zotero_data_dir: '', pdf_root: '' });

  const seededFormState = useMemo(
    () => configToFormState(configQuery.data),
    [configQuery.data],
  );

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
    if (saveMutation.isError) saveMutation.reset();
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function updatePathField(key, value) {
    setPathForm((prev) => ({ ...prev, [key]: value }));
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
        <h2 className="font-display text-xl font-light text-slate-900">Settings</h2>
        <p className="text-xs text-slate-500 mt-1">
          Edits replace <span className="font-mono">goals.yaml</span> on save.
          Changes apply immediately to in-flight daemon ticks.
        </p>
      </header>

      <ReadinessStrip />

      <form onSubmit={handleSave} className="space-y-4">
        <DeploymentCard />
        <AiModelsSection
          status={status}
          routing={form.llm_routing}
          onChange={(next) => updateField('llm_routing', next)}
          isDirty={isDirty}
          open={modelsOpen}
          onToggle={setModelsOpen}
        />

        <EssentialsSection
          form={form}
          onUpdate={updateField}
          pathForm={pathForm}
          onUpdatePath={updatePathField}
        />

        <RssFeedsSection />

        <details className="glass rounded-2xl border border-slate-200 p-4">
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">
            Advanced · performance &amp; library access
          </summary>
          <div className="space-y-4 mt-4">
            <UniversityAccessPanel form={form} onUpdate={updateField} />
            <CalibrationCard />
          </div>
        </details>

        <div className="sticky bottom-0 -mx-4 px-4 py-3 bg-white/95 backdrop-blur border-t border-slate-200 z-10 flex items-center gap-3">
          <Button type="submit" disabled={saving || !isDirty}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
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

    </div>
  );
}
