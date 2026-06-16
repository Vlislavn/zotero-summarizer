import { useCallback, useMemo, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { checkLlm, listModels } from '../api/settingsApi.js';
// StatusPill now lives with its badge siblings in ui/Badge.jsx; re-exported here
// so existing importers (CheckResults below, setup/StepConnectLlm) keep working.
import { StatusPill } from './ui/Badge.jsx';

export { StatusPill };

// LLM providers & per-stage routing editor for the Settings page.
//
// Backend contract: zotero_summarizer/models.py :: GoalsConfig.llm_routing.
// Shape (always present in GET responses — synthesized from the legacy `llm:`
// block for old configs):
//   {
//     providers: [
//       { name, type: 'openai'|'anthropic', base_url, api_key_env,
//         extra_body, max_tokens }, ...
//     ],
//     default:     { provider, model },
//     feed:        { provider|null, model|null },
//     backlog:     { provider|null, model|null },
//     deep_review: { provider|null, model|null },
//   }
//
// Inheritance-first (Tesler / Pareto): a stage with provider:null AND model:null
// inherits `default`. The UI defaults every stage row to "Inherit default" so
// the common case is "configure once, override per stage only when needed".
//
// We KEEP `extra_body` (and any other unknown provider keys) untouched in the
// payload — we just don't surface them in the UI.
//
// This component is controlled: it receives the structured `llm_routing` object
// as `value` and emits a brand-new object via `onChange` on every edit. It must
// never mutate `value` in place (it's the same reference React Query / the form
// dirty-check rely on), so every handler shallow/deep-clones the parts it
// touches.

const STAGES = [
  {
    key: 'feed',
    label: 'Feed',
    hint: 'Per-item triage scoring on the live RSS feed. High volume — usually a fast/cheap model.',
  },
  {
    key: 'backlog',
    label: 'Backlog',
    hint: 'Daily backlog selection pass. Lower volume — a stronger model is affordable here.',
  },
  {
    key: 'deep_review',
    label: 'Deep review',
    hint: 'Full-text deep reads. Lowest volume — your strongest / largest-context model.',
  },
];

const PROVIDER_TYPES = ['openai', 'anthropic'];
const DEFAULT_MAX_TOKENS = 4096;

const INPUT_CLS =
  'w-full mt-1 p-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500';

function emptyProvider() {
  return {
    name: '',
    type: 'openai',
    base_url: '',
    api_key_env: '',
    extra_body: null,
    max_tokens: DEFAULT_MAX_TOKENS,
  };
}

// --- Provider registry editor --------------------------------------------

function ProviderRow({ provider, index, onPatch, onRemove }) {
  const isOpenai = (provider.type || 'openai') === 'openai';
  return (
    <div className="rounded-xl border border-slate-200 bg-white/60 p-3 space-y-3">
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs font-semibold text-slate-700">Name</span>
          <input
            type="text"
            value={provider.name ?? ''}
            onChange={(e) => onPatch(index, { name: e.target.value })}
            placeholder="e.g. local, claude"
            className={INPUT_CLS}
          />
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-700">Type</span>
          <select
            value={provider.type || 'openai'}
            onChange={(e) => onPatch(index, { type: e.target.value })}
            className={`${INPUT_CLS} bg-white`}
          >
            {PROVIDER_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid sm:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs font-semibold text-slate-700">
            Base URL{' '}
            {isOpenai ? (
              <span className="text-rose-600">(required)</span>
            ) : (
              <span className="text-slate-400">(optional)</span>
            )}
          </span>
          <input
            type="text"
            value={provider.base_url ?? ''}
            onChange={(e) =>
              onPatch(index, { base_url: e.target.value === '' ? null : e.target.value })
            }
            placeholder={
              isOpenai ? 'http://localhost:11434/v1' : 'leave blank for default'
            }
            className={INPUT_CLS}
          />
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-700">
            API key env var
          </span>
          <input
            type="text"
            value={provider.api_key_env ?? ''}
            onChange={(e) => onPatch(index, { api_key_env: e.target.value })}
            placeholder="OPENAI_API_KEY"
            className={INPUT_CLS}
          />
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="block w-40">
          <span className="text-xs font-semibold text-slate-700">Max tokens</span>
          <input
            type="number"
            min={1}
            step={1}
            value={provider.max_tokens ?? DEFAULT_MAX_TOKENS}
            onChange={(e) =>
              onPatch(index, {
                max_tokens:
                  e.target.value === ''
                    ? DEFAULT_MAX_TOKENS
                    : Math.max(1, Math.round(Number(e.target.value))),
              })
            }
            className={INPUT_CLS}
          />
        </label>
        <button
          type="button"
          onClick={() => onRemove(index)}
          className="px-3 py-2 rounded-lg border border-rose-300 text-rose-700 text-sm hover:bg-rose-50"
        >
          Remove provider
        </button>
      </div>
      <p className="text-xs text-slate-500">
        The env var holds the API key&apos;s <span className="font-semibold">name</span>,
        never the secret itself. Reasoning models often need 16384 max tokens.
      </p>
    </div>
  );
}

// --- Per-stage / default routing rows -------------------------------------

// Inline status under a Model field: loading / error+retry / "N models — type
// to filter" + refresh. Doherty Threshold: the network fetch must give visible
// feedback right where the user is choosing.
function ModelHint({ entry, count, onRefresh }) {
  const status = entry?.status;
  if (!status || status === 'idle') return null;
  if (status === 'loading') {
    return <p className="text-xs text-slate-400 mt-1">Loading models…</p>;
  }
  if (status === 'error') {
    return (
      <p className="text-xs text-rose-600 mt-1">
        {entry.error || 'Could not list models.'}{' '}
        <button type="button" onClick={onRefresh} className="underline hover:no-underline">
          retry
        </button>
      </p>
    );
  }
  return (
    <p className="text-xs text-slate-400 mt-1">
      {count > 0 ? `${count} model${count === 1 ? '' : 's'} — type to filter` : 'No models returned'}{' '}
      <button type="button" onClick={onRefresh} className="underline hover:no-underline" title="Reload models">
        ↻
      </button>
    </p>
  );
}

function StageRow({
  label,
  hint,
  stage,
  rowKey,
  providerNames,
  allowInherit,
  defaultModel,
  defaultProviderName,
  catalog,
  onLoadModels,
  onChange,
}) {
  const providerValue = stage?.provider ?? '';
  const modelValue = stage?.model ?? '';
  // Blank model placeholder shows what would be inherited from `default`.
  const modelPlaceholder = allowInherit
    ? defaultModel
      ? `Inherit default (${defaultModel})`
      : 'Inherit default'
    : 'e.g. gpt-oss:20b';

  // The provider whose catalogue feeds this row's model suggestions: the row's
  // own provider, or — for an inheriting stage left on "Inherit default" — the
  // default's provider (the system resolves it; Tesler's Law).
  const effectiveProvider = providerValue || (allowInherit ? defaultProviderName : '');
  const entry = effectiveProvider ? catalog[effectiveProvider] : null;
  const models = entry?.models || [];
  const listId = `models-${rowKey}`;

  return (
    <div className="grid sm:grid-cols-[10rem_1fr_1fr] gap-3 items-start">
      <div className="pt-1">
        <span className="text-sm font-semibold text-slate-700">{label}</span>
        {hint && <p className="text-xs text-slate-500 mt-0.5">{hint}</p>}
      </div>
      <label className="block">
        <span className="text-xs font-semibold text-slate-700">Provider</span>
        <select
          value={providerValue}
          onChange={(e) => {
            const v = e.target.value;
            onChange({ ...stage, provider: v === '' ? null : v });
            // Auto-load the picked provider's catalogue so the model field is
            // ready to suggest immediately ("very easy way").
            const eff = v || (allowInherit ? defaultProviderName : '');
            if (eff) onLoadModels(eff);
          }}
          className={`${INPUT_CLS} bg-white`}
        >
          {allowInherit && <option value="">Inherit default</option>}
          {!allowInherit && providerNames.length === 0 && (
            <option value="">(add a provider above)</option>
          )}
          {providerNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="text-xs font-semibold text-slate-700">Model</span>
        {/* Combobox, not a locked dropdown (Postel's Law): the datalist offers
            the provider's models as type-ahead suggestions, but any id can still
            be typed (e.g. an Ollama model you'll pull later). */}
        <input
          type="text"
          list={models.length ? listId : undefined}
          value={modelValue}
          placeholder={modelPlaceholder}
          onFocus={() => {
            if (effectiveProvider) onLoadModels(effectiveProvider);
          }}
          onChange={(e) => {
            const v = e.target.value;
            onChange({ ...stage, model: v === '' ? (allowInherit ? null : '') : v });
          }}
          className={INPUT_CLS}
        />
        {models.length > 0 && (
          <datalist id={listId}>
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        )}
        {effectiveProvider && (
          <ModelHint
            entry={entry}
            count={models.length}
            onRefresh={() => onLoadModels(effectiveProvider, { force: true })}
          />
        )}
      </label>
    </div>
  );
}

// --- Operational check ----------------------------------------------------

export function CheckResults({ result }) {
  if (!result) return null;
  const degraded = result.status === 'degraded';
  return (
    <div className="space-y-2">
      <div
        role="status"
        aria-live="polite"
        className={`px-3 py-2 rounded-lg border text-sm ${
          degraded
            ? 'bg-amber-50 border-amber-200 text-amber-900'
            : 'bg-emerald-50 border-emerald-200 text-emerald-900'
        }`}
      >
        {degraded
          ? 'Some stages failed their probe (see below). The app keeps running regardless.'
          : 'All stages responded — routing is operational.'}
      </div>
      <ul className="space-y-1">
        {(result.stages || []).map((s) => (
          <li
            key={s.stage}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm border border-slate-200 rounded-lg px-3 py-2 bg-white/60"
          >
            <span className="font-semibold text-slate-700 w-24">{s.stage}</span>
            <StatusPill status={s.status} />
            <span className="text-xs text-slate-500 font-mono">
              {s.provider} · {s.type} · {s.model}
            </span>
            {s.status !== 'operational' && s.detail && (
              <span className="text-xs text-rose-700 basis-full">{s.detail}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- Section --------------------------------------------------------------

/**
 * Controlled editor for `goals.yaml`'s `llm_routing` block.
 *
 * Props:
 *   - value:    the structured llm_routing object (providers/default/stages).
 *   - onChange: (nextRouting) => void — receives a fresh object on every edit.
 *   - isDirty:  boolean — when true, the live probe warns "Save first" because
 *               the check runs against the server's *saved* config.
 */
export default function LlmRoutingSection({ value, onChange, isDirty }) {
  const routing = value || {};
  // Memoized so it's a stable dep for loadModels' useCallback (re-derives only
  // when the underlying providers array changes).
  const providers = useMemo(
    () => (Array.isArray(routing.providers) ? routing.providers : []),
    [routing.providers],
  );
  const providerNames = providers.map((p) => p?.name).filter(Boolean);
  const defaultRouting = routing.default || { provider: null, model: null };
  const defaultModel = defaultRouting.model || '';
  const defaultProviderName = defaultRouting.provider || '';

  const checkMutation = useMutation({ mutationFn: checkLlm });

  // Per-provider model catalogue cache (name -> {status, models, error}), shared
  // by every model field so we never re-list the same provider. The ref mirrors
  // state so loadModels can read the current cache without being recreated on
  // each fetch (avoids duplicate in-flight requests).
  const [catalog, setCatalog] = useState({});
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;

  const loadModels = useCallback(
    async (name, { force = false } = {}) => {
      if (!name) return;
      const provider = providers.find((p) => p?.name === name);
      if (!provider) return;
      const cur = catalogRef.current[name];
      if (!force && cur && (cur.status === 'loading' || cur.status === 'ok')) return;
      setCatalog((c) => ({
        ...c,
        [name]: { status: 'loading', models: cur?.models || [], error: '' },
      }));
      try {
        const data = await listModels(provider);
        setCatalog((c) => ({
          ...c,
          [name]: { status: 'ok', models: data.models || [], error: '' },
        }));
      } catch (err) {
        setCatalog((c) => ({
          ...c,
          [name]: { status: 'error', models: [], error: err?.message || 'Failed to list models' },
        }));
      }
    },
    [providers],
  );

  // Emit a fresh routing object with one branch replaced. Never mutate `value`.
  function emit(patch) {
    onChange({ ...routing, ...patch });
  }

  function patchProvider(index, fields) {
    const nextProviders = providers.map((p, i) =>
      i === index ? { ...p, ...fields } : p,
    );
    emit({ providers: nextProviders });
  }

  function addProvider() {
    emit({ providers: [...providers, emptyProvider()] });
  }

  function removeProvider(index) {
    emit({ providers: providers.filter((_, i) => i !== index) });
  }

  function setStage(key, nextStage) {
    emit({ [key]: nextStage });
  }

  return (
    <div className="space-y-5">
      {/* 1. Providers registry */}
      <div className="space-y-3">
        <div>
          <h4 className="text-sm font-semibold text-slate-800">Providers</h4>
          <p className="text-xs text-slate-500">
            Named LLM endpoints. <span className="font-mono">openai</span> needs a
            base URL (e.g. Ollama / vLLM); <span className="font-mono">anthropic</span>{' '}
            does not. Provider names must be unique.
          </p>
        </div>
        {providers.length === 0 && (
          <p className="text-xs text-slate-500 italic">
            No providers yet — add one to route any stage.
          </p>
        )}
        <div className="space-y-3">
          {providers.map((p, i) => (
            <ProviderRow
              key={i}
              provider={p}
              index={i}
              onPatch={patchProvider}
              onRemove={removeProvider}
            />
          ))}
        </div>
        <button
          type="button"
          onClick={addProvider}
          className="px-3 py-2 rounded-lg border border-slate-300 text-sm font-semibold hover:bg-slate-100"
        >
          + Add provider
        </button>
      </div>

      <div className="border-t border-slate-200" />

      {/* 2. Default + per-stage routing */}
      <div className="space-y-3">
        <div>
          <h4 className="text-sm font-semibold text-slate-800">Stage routing</h4>
          <p className="text-xs text-slate-500">
            Configure the <span className="font-semibold">default</span> once; each
            stage inherits it unless you override. Leaving a stage&apos;s provider on{' '}
            <span className="font-semibold">Inherit default</span> and its model
            blank means it follows the default. Pick a provider and the{' '}
            <span className="font-semibold">Model</span> field suggests that
            provider&apos;s available models — you can still type any id.
          </p>
        </div>

        <StageRow
          label="Default"
          hint="The fallback for every stage. Cannot inherit — must name a provider and model."
          stage={defaultRouting}
          rowKey="default"
          providerNames={providerNames}
          allowInherit={false}
          defaultModel={defaultModel}
          defaultProviderName={defaultProviderName}
          catalog={catalog}
          onLoadModels={loadModels}
          onChange={(next) => setStage('default', next)}
        />

        <div className="border-t border-dashed border-slate-200" />

        {STAGES.map(({ key, label, hint }) => (
          <StageRow
            key={key}
            rowKey={key}
            label={label}
            hint={hint}
            stage={routing[key] || { provider: null, model: null }}
            providerNames={providerNames}
            allowInherit
            defaultModel={defaultModel}
            defaultProviderName={defaultProviderName}
            catalog={catalog}
            onLoadModels={loadModels}
            onChange={(next) => setStage(key, next)}
          />
        ))}
      </div>

      <div className="border-t border-slate-200" />

      {/* 3. Operational check */}
      <div className="space-y-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-800">Operational check</h4>
          <p className="text-xs text-slate-500">
            Sends a tiny probe to each stage&apos;s configured provider. This is a
            safe, on-demand test of the <span className="font-semibold">saved</span>{' '}
            config — per-stage failures are informational and never block the app.
          </p>
        </div>
        {isDirty && (
          <div
            role="status"
            aria-live="polite"
            className="px-3 py-2 rounded-lg border text-sm bg-amber-50 border-amber-200 text-amber-900"
          >
            You have unsaved changes. Save first — the check probes the server&apos;s
            currently saved config, not your edits.
          </div>
        )}
        <button
          type="button"
          onClick={() => checkMutation.mutate()}
          disabled={checkMutation.isPending}
          className="px-4 py-2.5 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {checkMutation.isPending ? 'Checking…' : 'Check operational'}
        </button>
        {checkMutation.isError && (
          <div
            role="status"
            aria-live="polite"
            className="px-3 py-2 rounded-lg border text-sm bg-rose-50 border-rose-200 text-rose-800"
          >
            {checkMutation.error?.message || 'Probe request failed.'}
          </div>
        )}
        {checkMutation.isSuccess && <CheckResults result={checkMutation.data} />}
      </div>
    </div>
  );
}
