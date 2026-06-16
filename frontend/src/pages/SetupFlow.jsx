// First-run wizard orchestrator. Owns:
//   - the active step index (0..3)
//   - the shared `draft` form state, seeded from GET /api/config (so a returning
//     user resumes their real config, never a blank slate) + a sensible default
//     triage-criteria prefill
//   - per-step validity (which gates Next + the final Finish)
//
// Finish writes formStateToConfig(draft, baseConfig) via PUT /api/config, then
// invalidates ['setup-status'] and advances to the Done step. Skippable and
// resumable: "Skip for now" persists zs:setupDismissed=1 and routes home.

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
import { validateSetup } from '../api/setupApi.js';

// A sensible, editable default so the Describe step is never blank.
const DEFAULT_TRIAGE_CRITERIA = [
  'Directly advances one of my research goals',
  'Introduces a method, dataset, or result I could build on',
  'Strong venue or credible authors',
].join('\n');

export default function SetupFlow() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { status, pillars } = useSetupStatus();

  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });

  const [step, setStep] = useState(0);
  // Furthest step the user has actually reached. A step only earns its green
  // "done" check once it's been reached AND is valid — otherwise step 3, whose
  // prefilled defaults are valid from the start, would show done before the user
  // ever sees it (a false Goal-Gradient signal).
  const [maxStepReached, setMaxStepReached] = useState(0);
  const [draft, setDraft] = useState(null);
  // Tracks whether the LLM "Test connection" passed for the current fields.
  // Advisory only (populates the model datalist + the success pill) — it does
  // NOT gate Next: the secret lives outside the app (.env), so a first-run user
  // who hasn't set it yet must still be able to finish setup (Tesler's Law —
  // never trap the user). Next gates on structural validity instead.
  const [llmTestedOk, setLlmTestedOk] = useState(false);
  // Field errors from the last describe-step validate-config.
  const [fieldErrors, setFieldErrors] = useState([]);
  const [finishError, setFinishError] = useState('');

  // Path draft is separate from the GoalsConfig draft: paths are written via the
  // dedicated /api/setup/paths route, not the config PUT.
  const [draftPaths, setDraftPaths] = useState({ zotero_data_dir: '', pdf_root: '' });

  // Seed the GoalsConfig draft once the server config lands.
  useEffect(() => {
    if (configQuery.data && draft === null) {
      const seeded = configToFormState(configQuery.data);
      if (seeded && !seeded.triage_criteria_text) {
        seeded.triage_criteria_text = DEFAULT_TRIAGE_CRITERIA;
      }
      setDraft(seeded);
    }
  }, [configQuery.data, draft]);

  // Seed the path draft from the live status payload (the resolved values).
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
    const zoteroOk = Boolean(status?.zotero?.db_found);
    // LLM step: structurally well-formed config, NOT a passing live test. The
    // provider needs a name for the key env var, a base URL (openai only), and a
    // default model. The endpoint being reachable / the secret being exported are
    // surfaced as advisory signals, never as a gate (the app degrades gracefully
    // without a live LLM, and the secret is set outside the app).
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
  }, [status?.zotero?.db_found, draft]);

  const allValid = validity.every(Boolean);

  // Remember the furthest step reached so StepProgress only credits steps the
  // user has actually visited (monotonic — going Back keeps earlier checks).
  useEffect(() => { setMaxStepReached((m) => Math.max(m, step)); }, [step]);

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

  // Validate the GoalsConfig before saving so field errors surface inline.
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
    try {
      const res = await validateMutation.mutateAsync(payload);
      if (res && res.valid === false) {
        setFieldErrors(res.field_errors || []);
        setStep(2); // jump back to the describe step where the errors live
        return;
      }
    } catch {
      // Validation endpoint unreachable — fall through to the PUT, which
      // re-validates strictly server-side and surfaces its own error banner.
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
        {step === 3 && <StepDone pillars={pillars} />}

        {finishError && <Banner kind="error">{finishError}</Banner>}

        {step < 3 && (
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-slate-200">
            <button
              type="button"
              onClick={() => setStep((s) => Math.max(0, s - 1))}
              disabled={step === 0}
              className="px-4 py-2 rounded-lg border border-slate-300 text-sm font-medium hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Back
            </button>
            {isLast ? (
              <button
                type="button"
                onClick={handleFinish}
                disabled={!allValid || finishMutation.isPending || validateMutation.isPending}
                className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
                title={!allValid ? 'Complete all three steps to finish.' : undefined}
              >
                {finishMutation.isPending || validateMutation.isPending ? 'Saving…' : 'Finish'}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setStep((s) => Math.min(2, s + 1))}
                disabled={!stepValid}
                className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
                title={!stepValid ? 'Finish this step to continue.' : undefined}
              >
                Next
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
