// Slim default-provider editor for Settings → Essentials. Covers the common
// single-provider case by editing llm_routing.providers[0] + llm_routing.default
// in place. If the user has a power setup (multiple providers OR any per-stage
// override), we DON'T clobber it — we show a note + a link that opens Advanced.

import { Field } from '../form/Fields.jsx';

const PROVIDER_TYPES = ['openai', 'anthropic'];
const DEFAULT_MAX_TOKENS = 4096;
const STAGE_KEYS = ['feed', 'backlog', 'deep_review'];

// A stage "overrides" the default when it names a provider or a model of its own.
function hasStageOverride(routing) {
  return STAGE_KEYS.some((k) => {
    const s = routing?.[k];
    return Boolean(s && (s.provider || s.model));
  });
}

export function isPowerRouting(routing) {
  const providers = routing?.providers || [];
  return providers.length > 1 || hasStageOverride(routing);
}

export default function DefaultProviderField({ routing, onChange, onOpenAdvanced }) {
  const providers = Array.isArray(routing?.providers) ? routing.providers : [];

  if (isPowerRouting(routing)) {
    return (
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
        <p className="text-slate-700 font-medium">Multiple providers / custom routing configured</p>
        <p className="text-xs text-slate-500 mt-1">
          You have more than one provider or per-stage overrides. Edit them in{' '}
          <button
            type="button"
            onClick={onOpenAdvanced}
            className="text-teal-700 underline hover:text-teal-900 font-medium"
          >
            Advanced → LLM providers &amp; stage routing
          </button>{' '}
          so this slim editor doesn&apos;t flatten your setup.
        </p>
      </div>
    );
  }

  const provider = providers[0] || {
    name: 'default',
    type: 'openai',
    base_url: '',
    api_key_env: '',
    extra_body: null,
    max_tokens: DEFAULT_MAX_TOKENS,
  };
  const isOpenai = (provider.type || 'openai') === 'openai';
  const defaultModel = routing?.default?.model || '';

  function patchProvider(fields) {
    const next = { ...provider, ...fields };
    onChange({
      ...routing,
      providers: [next],
      default: { ...(routing?.default || {}), provider: next.name || 'default' },
    });
  }

  function patchModel(model) {
    onChange({
      ...routing,
      providers: providers.length ? providers : [provider],
      default: {
        ...(routing?.default || {}),
        provider: provider.name || 'default',
        model: model || null,
      },
    });
  }

  return (
    <div className="space-y-3">
      <div className="grid md:grid-cols-2 gap-4">
        <Field
          kind="select"
          label="Provider type"
          value={provider.type || 'openai'}
          onChange={(v) => patchProvider({ type: v })}
          options={PROVIDER_TYPES}
          hint="openai = any OpenAI-compatible server. anthropic = Claude."
        />
        <Field
          label="Base URL"
          value={provider.base_url || ''}
          onChange={(v) => patchProvider({ base_url: v === '' ? null : v })}
          placeholder={isOpenai ? 'http://localhost:11434/v1' : 'leave blank for default'}
          hint={isOpenai ? 'Required for openai-type providers.' : 'Optional for anthropic.'}
        />
        <Field
          label="API key env var (NAME)"
          value={provider.api_key_env || ''}
          onChange={(v) => patchProvider({ api_key_env: v })}
          placeholder="OPENAI_API_KEY"
          hint="This is the env var NAME — set the secret value in .env / your shell, not here."
        />
        <Field
          label="Default model"
          value={defaultModel}
          onChange={patchModel}
          placeholder="e.g. gpt-oss:20b"
          hint="Used for every stage unless overridden in Advanced."
        />
      </div>
    </div>
  );
}
