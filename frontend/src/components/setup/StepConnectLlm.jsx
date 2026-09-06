import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { listModels } from '../../api/settingsApi.js';
import { fetchAiPresets, saveAiCredential } from '../../api/setupApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const INPUT = 'w-full mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500';

function activeProvider(routing) {
  const name = routing?.default?.provider;
  return (routing?.providers || []).find((provider) => provider.name === name)
    || (routing?.providers || [])[0];
}

function presetFor(provider, presets) {
  if (!provider) return 'openrouter';
  return presets.find((preset) => (
    preset.provider?.type === provider.type
    && (preset.provider?.base_url || '') === (provider.base_url || '')
  ))?.id || 'custom';
}

export default function StepConnectLlm({ status, routing, onPatchRouting, testedOk, onTested }) {
  const presetsQuery = useQuery({ queryKey: ['ai-presets'], queryFn: fetchAiPresets });
  const presets = presetsQuery.data?.presets || [];
  const localCatalog = presetsQuery.data?.local_profiles;
  const current = activeProvider(routing);
  const [choice, setChoice] = useState(null);
  const [localProfile, setLocalProfile] = useState(null);
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState([]);
  const selectedId = choice || presetFor(current, presets);
  const selected = presets.find((preset) => preset.id === selectedId);
  const selectedLocal = localCatalog?.profiles.find((profile) => profile.id === localProfile);
  const provider = selectedId === 'custom' ? current : selectedLocal?.provider || selected?.provider;
  const defaultModel = routing?.default?.model || '';

  function patchProvider(next, clearModel = false) {
    const old = routing?.default?.provider;
    const oldIsRouted = ['feed', 'backlog', 'deep_review'].some((key) => routing?.[key]?.provider === old);
    const providers = (routing?.providers || []).filter((item) =>
      item.name !== next.name && (item.name !== old || oldIsRouted));
    providers.push(next);
    onPatchRouting({
      ...routing,
      providers,
      default: { ...(routing?.default || {}), provider: next.name,
        model: clearModel ? null : routing?.default?.model },
    });
    onTested?.(false);
    setModels([]);
  }

  function choosePreset(id) {
    setChoice(id);
    setLocalProfile(null);
    setApiKey('');
    const preset = presets.find((item) => item.id === id);
    if (preset?.provider) patchProvider(preset.provider, true);
  }

  function chooseLocalProfile(profile) {
    const local = profile.provider;
    if (!local || !profile.compatible) return;
    const providers = (routing?.providers || []).filter((item) => item.name !== local.name);
    providers.push(local);
    const model = profile.model || null;
    setLocalProfile(profile.id);
    onPatchRouting({
      ...routing, providers,
      default: { provider: local.name, model },
      feed: { provider: local.name, model },
      backlog: { provider: local.name, model },
      deep_review: { provider: local.name, model },
    });
    onTested?.(false);
    setModels([]);
  }

  function patchCustom(fields) {
    patchProvider({
      name: current?.name || 'custom', type: current?.type || 'openai',
      base_url: current?.base_url ?? '', api_key_env: current?.api_key_env ?? '',
      max_tokens: current?.max_tokens || 4096, ...fields,
    });
  }

  function patchModel(model) {
    const next = { ...routing,
      default: { ...(routing?.default || {}), provider: provider?.name, model: model || null } };
    if (selectedId === 'local') {
      ['feed', 'backlog', 'deep_review'].forEach((stage) => {
        next[stage] = { provider: provider?.name, model: model || null };
      });
    }
    onPatchRouting(next);
  }

  const connectMutation = useMutation({
    mutationFn: async () => {
      if (requiresKey && apiKey) {
        await saveAiCredential(provider.api_key_env, apiKey);
      }
      return listModels(provider);
    },
    onSuccess: (data) => {
      setModels(data.models || []);
      setApiKey('');
      onTested?.(true);
    },
    onError: () => onTested?.(false),
  });

  const requiresKey = selectedId !== 'local';
  const legacyStored = status?.llm?.api_key_env === provider?.api_key_env
    && status?.llm?.api_key_present;
  const stored = Boolean(selected?.credential?.present || legacyStored);
  const canConnect = Boolean(provider && (selectedId !== 'local' || localProfile)
    && (!requiresKey || apiKey || stored));
  const connected = Boolean(testedOk && connectMutation.isSuccess && models.includes(defaultModel));
  const cardClass = (id) => `text-left rounded-xl border px-3 py-2 transition-colors ${
    selectedId === id ? 'border-forest-800 bg-forest-800/5' : 'border-slate-200 hover:bg-slate-50'
  }`;

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-900">Connect AI</h3>
        <p className="text-sm text-slate-500 mt-1">Choose a service, paste its key, then pick one model.</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" aria-label="AI services">
        {presets.map((preset) => (
          <button key={preset.id} type="button" className={cardClass(preset.id)} onClick={() => choosePreset(preset.id)}>
            <span className="block text-sm font-semibold text-slate-800">{preset.label}</span>
            {preset.recommended && <span className="text-[11px] text-forest-800">Recommended</span>}
          </button>
        ))}
      </div>

      {presetsQuery.isError && <Banner kind="error">Could not load AI services.</Banner>}

      {selectedId === 'local' && localCatalog && (
        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold text-slate-700">Choose a local profile</legend>
          <p className="text-xs text-slate-500">
            Detected {localCatalog.hardware.memory_gb} GB memory and {localCatalog.hardware.disk_free_gb} GB free disk.
          </p>
          <div className="grid sm:grid-cols-3 gap-2">
            {localCatalog.profiles.map((profile) => (
              <button key={profile.id} type="button" disabled={!profile.compatible}
                aria-pressed={localProfile === profile.id}
                className={`text-left rounded-xl border px-3 py-2 ${
                  localProfile === profile.id ? 'border-forest-800 bg-forest-800/5' : 'border-slate-200'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
                onClick={() => chooseLocalProfile(profile)}>
                <span className="block text-sm font-semibold text-slate-800">{profile.label}</span>
                <span className="block text-xs text-slate-500 mt-1">
                  {profile.model || 'Your model'}{profile.size_gb ? ` · ${profile.size_gb} GB` : ''}
                </span>
                <span className="block text-[11px] text-slate-500 mt-1">{profile.compatibility_detail}</span>
              </button>
            ))}
          </div>
          {selectedLocal && (
            <div className="rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs text-slate-600">
              <p>{selectedLocal.runtime} · {selectedLocal.features.join(', ')}</p>
              <p className="mt-1">{selectedLocal.tradeoff}</p>
              {selectedLocal.source && <a className="underline" href={selectedLocal.source}
                target="_blank" rel="noreferrer">Official model source</a>}
              {selectedLocal.pull_command && <>
                <p className="mt-2">Download starts only when you run this explicit command:</p>
                <code className="block my-1 select-all text-slate-900">{selectedLocal.pull_command}</code>
                <Button type="button" variant="secondary"
                  onClick={() => navigator.clipboard?.writeText(selectedLocal.pull_command)}>
                  Copy command
                </Button>
              </>}
            </div>
          )}
        </fieldset>
      )}

      {selectedId === 'custom' && (
        <div className="grid sm:grid-cols-2 gap-3 rounded-xl border border-slate-200 p-3">
          <Field kind="select" label="Protocol" value={current?.type || 'openai'}
            onChange={(type) => patchCustom({ type })} options={['openai', 'anthropic']} />
          <Field label="Endpoint URL" value={current?.base_url || ''}
            onChange={(base_url) => patchCustom({ base_url })} />
          <Field label="Credential name" value={current?.api_key_env || ''}
            onChange={(api_key_env) => patchCustom({ api_key_env })} />
        </div>
      )}

      {selectedId !== 'local' && (
        <label className="block">
          <span className="text-sm font-semibold text-slate-700">API key</span>
          <input type="password" value={apiKey} autoComplete="off"
            placeholder={stored ? 'Stored securely — paste to replace' : 'Paste API key'}
            onChange={(event) => { setApiKey(event.target.value); onTested?.(false); }} className={INPUT} />
          <span className="block text-xs text-slate-500 mt-1">Saved in your operating-system credential store.</span>
        </label>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <Button variant="secondary" onClick={() => connectMutation.mutate()}
          disabled={!canConnect || connectMutation.isPending}>
          {connectMutation.isPending ? 'Connecting…' : 'Connect & load models'}
        </Button>
        {connected && <span className="text-sm font-semibold text-emerald-700">✓ Connected</span>}
      </div>

      {models.length > 0 && (
        <label className="block">
          <span className="text-sm font-semibold text-slate-700">Model</span>
          <select value={defaultModel} onChange={(event) => patchModel(event.target.value)} className={`${INPUT} bg-white`}>
            <option value="">Choose a model</option>
            {models.map((model) => <option key={model} value={model}>{model}</option>)}
          </select>
          <span className="block text-xs text-slate-500 mt-1">Used for feed triage, backlog, and deep reviews.</span>
        </label>
      )}

      {connectMutation.isError && <Banner kind="error">{humanizeError(connectMutation.error)}</Banner>}
    </div>
  );
}
