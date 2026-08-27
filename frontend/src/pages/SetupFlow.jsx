// Skippable, resumable Zotero → AI → research wizard over the shared config APIs.

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

const DEFAULT_TRIAGE_CRITERIA = [
  'Directly advances one of my research goals',
  'Introduces a method, dataset, or result I could build on',
  'Strong venue or credible authors',
].join('\n');
const PROGRESS_KEY = 'zs_setup_progress_v1';

function savedProgress() {
  try { return JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{}'); } catch { return {}; }
}

export default function SetupFlow() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { status } = useSetupStatus();

  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });

  const saved = useMemo(savedProgress, []);
  const [step, setStep] = useState(saved.step || 0);
  const [maxStepReached, setMaxStepReached] = useState(saved.maxStepReached || 0);
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
    const llmOk = Boolean(
      provider
        && provider.api_key_env && String(provider.api_key_env).trim()
        && (provider.type !== 'openai' || (provider.base_url && String(provider.base_url).trim()))
        && draft?.llm_routing?.default?.model
        && String(draft.llm_routing.default.model).trim(),
    );
    const goalsOk = Boolean(
      draft && draft.research_goals_text && draft.research_goals_text.trim().length > 0,
    );
    return [zoteroOk, llmOk, goalsOk];
  }, [draft]);

  const allValid = validity.every(Boolean);

  useEffect(() => { setMaxStepReached((m) => Math.max(m, step)); }, [step]);

  useEffect(() => {
    if (draft) localStorage.setItem(PROGRESS_KEY, JSON.stringify({
      step, maxStepReached, draft, draftPaths, pathsChanged,
    }));
  }, [step, maxStepReached, draft, draftPaths, pathsChanged]);

  const finishMutation = useMutation({
    mutationFn: (payload) => updateConfig(payload),
    onSuccess: (resp) => {
      if (resp?.config) {
        queryClient.setQueryData(['runtime-config'], resp.config);
      } else {
        queryClient.invalidateQueries({ queryKey: ['runtime-config'] });
      }
      queryClient.invalidateQueries({ queryKey: ['setup-status'] });
      setStep(3);
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
      setStep(2);
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

  const stepValid = step < 3 ? validity[step] : true;
  const isLast = step === 2;

  return (
    <div className="max-w-2xl mx-auto pb-10">
      <div className="glass rounded-2xl border border-slate-200 p-5 space-y-5">
        <header className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-lg font-bold text-slate-900">Set up Zotero Summarizer</h2>
            {step < 3 && (
              <button
                type="button"
                onClick={handleSkip}
                className="text-xs text-slate-500 hover:text-slate-800 underline"
              >
                Skip for now
              </button>
            )}
          </div>
          {step < 3 && (
            <StepProgress current={step} validity={validity} maxReached={maxStepReached} />
          )}
        </header>

        {step === 0 && (
          <StepConnectZotero
            status={status}
            draftPaths={draftPaths}
            onPatchPaths={(p) => setDraftPaths((prev) => ({ ...prev, ...p }))}
            onStatusChanged={() => queryClient.invalidateQueries({ queryKey: ['setup-status'] })}
            onPathsSaved={() => setPathsChanged(true)}
          />
        )}
        {step === 1 && (
          <StepConnectLlm
            status={status}
            routing={draft.llm_routing}
            onPatchRouting={patchRouting}
            testedOk={llmTestedOk}
            onTested={setLlmTestedOk}
          />
        )}
        {step === 2 && (
          <StepDescribeResearch
            draft={draft}
            onPatchDraft={patchDraft}
            fieldErrors={fieldErrors}
          />
        )}
        {step === 3 && <StepDone pathsChanged={pathsChanged} />}

        {finishError && <Banner kind="error">{finishError}</Banner>}

        {step < 3 && (
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-slate-200">
            <Button
              variant="secondary"
              onClick={() => setStep((s) => Math.max(0, s - 1))}
              disabled={step === 0}
            >
              Back
            </Button>
            {isLast ? (
              <Button
                onClick={handleFinish}
                disabled={!allValid || finishMutation.isPending || validateMutation.isPending}
                title={!allValid ? 'Complete the LLM and research steps to finish.' : undefined}
              >
                {finishMutation.isPending || validateMutation.isPending ? 'Saving…' : 'Finish'}
              </Button>
            ) : (
              <Button
                onClick={() => setStep((s) => Math.min(2, s + 1))}
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
