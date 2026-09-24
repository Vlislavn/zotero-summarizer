// Skippable, resumable mode → Zotero → model (if selected) → research wizard.

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchConfig, updateConfig } from '../api/settingsApi.js';
import { configToFormState, formStateToConfig } from '../utils/configForm.js';
import { humanizeError } from '../utils/humanizeError.js';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import { dismissSetup } from '../components/setup/SetupGate.jsx';
import StepProgress from '../components/setup/StepProgress.jsx';
import StepConnectZotero from '../components/setup/StepConnectZotero.jsx';
import StepConnectLlm from '../components/setup/StepConnectLlm.jsx';
import StepDescribeResearch from '../components/setup/StepDescribeResearch.jsx';
import StepDone from '../components/setup/StepDone.jsx';
import { Banner } from '../components/form/Fields.jsx';
import Button from '../components/ui/Button.jsx';
import { validateSetup } from '../api/setupApi.js';
import { readStoredJson, writeStorage } from '../utils/safeStorage.js';

const DEFAULT_TRIAGE_CRITERIA = [
  'Directly advances one of my research goals',
  'Introduces a method, dataset, or result I could build on',
  'Strong venue or credible authors',
].join('\n');
const STEP_LABELS = ['Choose mode', 'Zotero sync', 'Connect LLM', 'Describe research'];
const PROGRESS_KEY = 'zs_setup_progress_v1';

function savedProgress() {
  return readStoredJson(PROGRESS_KEY, {}, (value) => value && typeof value === 'object' && !Array.isArray(value));
}

function modeMatchesStages(routing, mode) {
  if (mode === 'none') return true;
  if (!routing?.default?.model) return false;
  const providers = new Map((routing.providers || []).map((provider) => [provider.name, provider]));
  return ['default', 'feed', 'backlog', 'deep_review'].every((stage) => {
    const selection = routing[stage] || {};
    const provider = providers.get(selection.provider || routing.default.provider);
    const model = selection.model || routing.default.model;
    if (!provider || !model) return false;
    if (!provider.base_url && provider.type !== 'anthropic') return false;
    let local = false;
    if (provider.base_url) {
      try {
        const host = new URL(provider.base_url).hostname.replace(/^\[|\]$/g, '').toLowerCase();
        local = ['localhost', '127.0.0.1', '::1'].includes(host);
      } catch { return false; }
    }
    return mode === 'local' ? local : !local;
  });
}

export default function SetupFlow() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { status } = useSetupStatus();

  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });

  const saved = useMemo(savedProgress, []);
  const [mode, setMode] = useState(saved.mode || null);
  const [step, setStep] = useState(saved.mode ? (saved.step || 0) : 0);
  const [maxStepReached, setMaxStepReached] = useState(saved.mode ? (saved.maxStepReached || 0) : 0);
  const [draft, setDraft] = useState(saved.draft || null);
  const [llmTestedOk, setLlmTestedOk] = useState(false);
  const [fieldErrors, setFieldErrors] = useState([]);
  const [finishError, setFinishError] = useState('');
  const [pathsChanged, setPathsChanged] = useState(Boolean(saved.pathsChanged));

  const [draftPaths, setDraftPaths] = useState(saved.draftPaths || { zotero_data_dir: '', pdf_root: '' });

  useEffect(() => {
    if (configQuery.data && draft === null) {
      const seeded = configToFormState(configQuery.data);
      if (seeded?.research_goals_text?.startsWith('Replace with your ')) seeded.research_goals_text = '';
      if (seeded && !seeded.triage_criteria_text) {
        seeded.triage_criteria_text = DEFAULT_TRIAGE_CRITERIA;
      }
      setDraft(seeded);
    }
  }, [configQuery.data, draft]);

  useEffect(() => {
    if (status?.paths) {
      setDraftPaths((prev) => {
        if (prev.zotero_data_dir || prev.pdf_root) return prev;
        return {
          zotero_data_dir: status.paths.zotero_data_dir?.value || '',
          pdf_root: status.paths.pdf_root?.value || '',
        };
      });
    }
  }, [status?.paths]);

  const validity = useMemo(() => {
    const zoteroOk = true;
    const provider = (draft?.llm_routing?.providers || [])[0];
    const llmOk = draft?.llm_enabled === false || Boolean(
      provider
        && provider.api_key_env && String(provider.api_key_env).trim()
        && (provider.type !== 'openai' || (provider.base_url && String(provider.base_url).trim()))
        && draft?.llm_routing?.default?.model
        && String(draft.llm_routing.default.model).trim(),
    );
    const goalsOk = Boolean(
      draft && draft.research_goals_text && draft.research_goals_text.trim().length > 0,
    );
    return [Boolean(mode), zoteroOk,
      mode === 'none' || (llmOk && modeMatchesStages(draft?.llm_routing, mode)), goalsOk];
  }, [draft, mode]);

  const allValid = validity.every(Boolean);

  useEffect(() => { setMaxStepReached((m) => Math.max(m, step)); }, [step]);

  useEffect(() => {
    if (draft) writeStorage(PROGRESS_KEY, JSON.stringify({
      step, maxStepReached, draft, draftPaths, pathsChanged, mode,
    }));
  }, [step, maxStepReached, draft, draftPaths, pathsChanged, mode]);

  const finishMutation = useMutation({
    mutationFn: (payload) => updateConfig(payload),
    onSuccess: (resp) => {
      if (resp?.config) {
        queryClient.setQueryData(['runtime-config'], resp.config);
      } else {
        queryClient.invalidateQueries({ queryKey: ['runtime-config'] });
      }
      queryClient.invalidateQueries({ queryKey: ['setup-status'] });
      queryClient.removeQueries({ queryKey: ['setup-doctor'] });
      setStep(4);
    },
    onError: (err) => setFinishError(humanizeError(err)),
  });

  const validateMutation = useMutation({
    mutationFn: (cfg) => validateSetup({ config: cfg, test_connection: false }),
  });

  function patchDraft(fields) {
    setDraft((prev) => (prev ? { ...prev, ...fields } : prev));
  }

  function patchRouting(nextRouting) {
    setDraft((prev) => (prev ? { ...prev, llm_routing: nextRouting } : prev));
  }

  async function handleFinish() {
    if (!draft || !configQuery.data) return;
    setFinishError('');
    const payload = formStateToConfig(draft, configQuery.data);
    // Field-level validation first; show inline errors and stop if invalid.
    const res = await validateMutation.mutateAsync(payload).catch(() => null);
    if (res && res.valid === false) {
      setFieldErrors(res.field_errors || []);
      setStep(3);
      return;
    }
    setFieldErrors([]);
    finishMutation.mutate(payload);
  }

  function handleSkip() {
    dismissSetup();
    navigate('/library');
  }

  if (configQuery.isLoading || !draft) {
    return (
      <div className="glass rounded-2xl border border-slate-200 p-6 text-sm text-slate-500">
        Preparing setup…
      </div>
    );
  }

  const stepValid = step < 4 ? validity[step] : true;
  const isLast = step === 3;
  const labels = mode === 'none' ? STEP_LABELS.filter((_, i) => i !== 2) : STEP_LABELS;
  const progressStep = mode === 'none' && step === 3 ? 2 : step;

  return (
    <div className="max-w-2xl mx-auto pb-10">
      <div className="glass rounded-2xl border border-slate-200 p-5 space-y-5">
        <header className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-lg font-bold text-slate-900">Set up Zotero Summarizer</h2>
            {step < 4 && (
              <button
                type="button"
                onClick={handleSkip}
                className="text-xs text-slate-500 hover:text-slate-800 underline"
              >
                Skip for now
              </button>
            )}
          </div>
          {step < 4 && (
            <StepProgress current={progressStep} validity={mode === 'none' ? validity.filter((_, i) => i !== 2) : validity}
              maxReached={mode === 'none' && maxStepReached >= 3 ? 2 : maxStepReached} labels={labels} />
          )}
          {step < 4 && (
            <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
              Step {progressStep + 1} of {labels.length}: {STEP_LABELS[step]}
            </p>
          )}
        </header>

        {step === 0 && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-slate-800">How should summaries run?</legend>
            {[
              ['local', 'Full local', 'All inference stays on this machine. Choose a compatible profile next.'],
              ['hosted', 'Hosted model', 'Connect an AI service with a key.'],
              ['none', 'Triage without an LLM', 'Classifier/search only. AI reviews, Ask Paper and adding new feed papers to Zotero stay off; manual Zotero imports still work.'],
            ].map(([id, label, description]) => (
              <label key={id} className="flex items-start gap-2 rounded-lg border border-slate-200 p-3 cursor-pointer">
                <input type="radio" name="setup-mode" className="mt-0.5" checked={mode === id}
                  onChange={() => { setMode(id); patchDraft({ llm_enabled: id !== 'none' }); }} />
                <span><span className="block text-sm font-medium">{label}</span>
                  <span className="block text-xs text-slate-500">{description}</span></span>
              </label>
            ))}
          </fieldset>
        )}
        {step === 1 && (
          <StepConnectZotero
            status={status}
            draftPaths={draftPaths}
            onPatchPaths={(p) => setDraftPaths((prev) => ({ ...prev, ...p }))}
            onStatusChanged={() => queryClient.invalidateQueries({ queryKey: ['setup-status'] })}
            onPathsSaved={() => setPathsChanged(true)}
          />
        )}
        {step === 2 && mode !== 'none' && (
          <StepConnectLlm status={status} routing={draft.llm_routing} mode={mode}
            onPatchRouting={patchRouting} testedOk={llmTestedOk} onTested={setLlmTestedOk} />
        )}
        {step === 3 && (
          <StepDescribeResearch
            draft={draft}
            onPatchDraft={patchDraft}
            fieldErrors={fieldErrors}
          />
        )}
        {step === 4 && <StepDone pathsChanged={pathsChanged} />}

        {finishError && <Banner kind="error">{finishError}</Banner>}

        {step < 4 && (
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-slate-200">
            <Button
              variant="secondary"
              onClick={() => setStep((s) => mode === 'none' && s === 3 ? 1 : Math.max(0, s - 1))}
              disabled={step === 0}
            >
              Back
            </Button>
            {isLast ? (
              <Button
                onClick={handleFinish}
                disabled={!allValid || finishMutation.isPending || validateMutation.isPending}
                title={!allValid ? 'Choose an AI mode and add your research goals to finish.' : undefined}
              >
                {finishMutation.isPending || validateMutation.isPending ? 'Saving…' : 'Finish'}
              </Button>
            ) : (
              <Button
                onClick={() => setStep((s) => mode === 'none' && s === 1 ? 3 : Math.min(3, s + 1))}
                disabled={!stepValid}
                title={!stepValid ? 'Finish this step to continue.' : undefined}
              >
                Next
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
