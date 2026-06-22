// Wizard step 2 — Connect LLM. Edits the FIRST provider in llm_routing.providers
// and the default stage. Collects provider type / base_url / api_key_env (the
// env-var NAME, never the secret) + the default model. A [Test connection]
// button probes the live endpoint via listModels — on success it fills the
// model datalist and shows a success pill. The test is ADVISORY: it does not
// gate Next (the secret lives in .env, outside the app, so a first-run user must
// be able to finish setup before the endpoint is reachable). Next gates on
// structural validity, computed in SetupFlow.
//
// API SECRET = NAME ONLY: the api_key_env field collects the env-var NAME; the
// "set in environment?" indicator comes from the setup-status payload
// (llm.api_key_present). The UI never has a field for the raw secret value.

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { listModels } from '../../api/settingsApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, Field } from '../form/Fields.jsx';
import { StatusPill } from '../LlmRoutingSection.jsx';
import Button from '../ui/Button.jsx';

const PROVIDER_TYPES = ['openai', 'anthropic'];
const DEFAULT_MAX_TOKENS = 4096;

// Read the single provider the wizard manages (index 0). Returns a normalized
// provider object so the controls never touch undefined.
function firstProvider(routing) {
  const p = (routing?.providers || [])[0];
  return {
    name: p?.name || 'default',
    type: p?.type || 'openai',
    base_url: p?.base_url ?? '',
    api_key_env: p?.api_key_env ?? '',
    extra_body: p?.extra_body ?? null,
    max_tokens: p?.max_tokens ?? DEFAULT_MAX_TOKENS,
  };
}

export default function StepConnectLlm({ routing, onPatchRouting, testedOk, onTested }) {
  const provider = firstProvider(routing);
  const isOpenai = provider.type === 'openai';
  const defaultModel = routing?.default?.model || '';
  const [models, setModels] = useState([]);

  const testMutation = useMutation({
    mutationFn: () => listModels(provider),
    onSuccess: (data) => {
      setModels(data?.models || []);
      // Mark the CURRENT provider fields as tested-OK. Any later edit to the
      // provider clears this (see patchProvider), so Next re-gates correctly.
      onTested?.(true);
    },
    onError: () => onTested?.(false),
  });

  // Patch the single provider, keeping providers[0] in place. Editing provider
  // fields invalidates a prior successful test.
  function patchProvider(fields) {
    const providers = Array.isArray(routing?.providers) ? [...routing.providers] : [];
    const next = { ...firstProvider(routing), ...fields };
    providers[0] = next;
    onPatchRouting({
      ...routing,
      providers,
      // Keep the default stage pointed at this provider by name.
      default: { ...(routing?.default || {}), provider: next.name },
    });
    onTested?.(false);
    setModels([]);
  }

  function patchDefaultModel(model) {
    onPatchRouting({
      ...routing,
      default: { ...(routing?.default || {}), provider: provider.name, model: model || null },
    });
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Connect an LLM</h3>
        <p className="text-sm text-slate-500 mt-1">
          Register one OpenAI-compatible or Anthropic endpoint. You can add more
          providers and per-stage routing later in Settings → Advanced.
        </p>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <Field
          kind="select"
          label="Provider type"
          value={provider.type}
          onChange={(v) => patchProvider({ type: v })}
          options={PROVIDER_TYPES}
          hint="openai = any OpenAI-compatible server (Ollama, vLLM, …). anthropic = Claude."
        />
        <Field
          label="Base URL"
          value={provider.base_url || ''}
          onChange={(v) => patchProvider({ base_url: v === '' ? null : v })}
          placeholder={isOpenai ? 'http://localhost:11434/v1' : 'leave blank for default'}
          hint={isOpenai ? 'Required for openai-type providers.' : 'Optional for anthropic.'}
        />
      </div>

      <Field
        label="API key env var"
        value={provider.api_key_env || ''}
        onChange={(v) => patchProvider({ api_key_env: v })}
        placeholder="OPENAI_API_KEY"
        hint="This is the env var NAME — set the secret value in .env / your shell, not here."
      />

      {/* Default model — a native combobox so the test's models become
          type-ahead suggestions (Field has no `list` prop), while still
          accepting any id you'll pull later (Postel's Law). */}
      <label className="block">
        <span className="text-sm font-semibold text-slate-700">Default model</span>
        <input
          type="text"
          list={models.length ? 'setup-llm-models' : undefined}
          value={defaultModel}
          placeholder="e.g. gpt-oss:20b"
          onChange={(e) => patchDefaultModel(e.target.value)}
          className="w-full mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
        />
        {models.length > 0 && (
          <datalist id="setup-llm-models">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        )}
        <span className="text-xs text-slate-500 mt-1 block">
          Used for every stage unless overridden in Advanced. Test below to populate suggestions.
        </span>
      </label>

      <div className="flex items-center gap-3 flex-wrap">
        <Button
          variant="secondary"
          onClick={() => testMutation.mutate()}
          disabled={testMutation.isPending || (isOpenai && !provider.base_url)}
        >
          {testMutation.isPending ? 'Testing…' : 'Test connection'}
        </Button>
        {testedOk && testMutation.isSuccess && <StatusPill status="operational" />}
        {testMutation.isSuccess && (
          <span className="text-xs text-slate-500">
            {models.length} model{models.length === 1 ? '' : 's'} found.
          </span>
        )}
      </div>

      {testMutation.isError && (
        <Banner kind="error">
          {humanizeError(testMutation.error)}
          {' '}You can still continue and fix this later in Settings.
        </Banner>
      )}
    </div>
  );
}
