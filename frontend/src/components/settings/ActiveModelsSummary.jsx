// Read-only "what's running now" card — the top of the AI Models region.
//
// Answers the user's "it's not clear what is selected now and where": one row
// per pipeline stage (Feed / Backlog / Deep review) showing the RESOLVED
// provider · model (inheritance applied client-side, no probe), the configured
// thinking-effort + temperature, and a live reachability dot. Clicking a row
// opens the editor below (onEdit). Pure display — the editor stays the single
// place that writes config (Occam: one editor, not two).

import { useQuery } from '@tanstack/react-query';
import { fetchLlmReachability } from '../../api/settingsApi.js';
import { resolveStage } from '../../utils/configForm.js';
import { ActionBadge } from '../ui/Badge.jsx';

const STAGES = [
  { key: 'feed', label: 'Feed' },
  { key: 'backlog', label: 'Backlog' },
  { key: 'deep_review', label: 'Deep review' },
];

// Effective thinking for the badge: the typed level wins; otherwise fall back to
// the legacy per-provider enable_thinking flag; otherwise "Default" (the provider
// default / deep_review decides per call).
function thinkingLabel(provider) {
  if (!provider) return '—';
  if (provider.thinking_effort) return provider.thinking_effort;
  const flag = provider.extra_body?.chat_template_kwargs?.enable_thinking;
  if (flag === true) return 'on';
  if (flag === false) return 'off';
  return 'Default';
}

// Anthropic ignores temperature (Opus rejects the param); show n/a so the user
// isn't misled into thinking a value there does anything.
function tempLabel(provider) {
  if (!provider) return '—';
  if (provider.type === 'anthropic') return 'temp n/a';
  return `temp ${provider.temperature ?? 0}`;
}

function LiveDot({ status }) {
  const tone =
    status === 'up'
      ? 'bg-emerald-500'
      : status === 'down'
        ? 'bg-rose-500'
        : 'bg-slate-300';
  const title =
    status === 'up' ? 'endpoint reachable' : status === 'down' ? 'endpoint unreachable' : 'reachability unknown';
  return <span className={`inline-block w-2 h-2 rounded-full ${tone}`} title={title} aria-hidden />;
}

export default function ActiveModelsSummary({ routing, onEdit }) {
  // Cheap, no-token reachability for the live dot. Not auto-refetched aggressively —
  // it's a glance, not a monitor.
  const reach = useQuery({
    queryKey: ['llm-reachability'],
    queryFn: fetchLlmReachability,
    staleTime: 30_000,
    retry: false,
  });
  const reachByStage = {};
  for (const row of reach.data?.stages || []) reachByStage[row.stage] = row;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h4 className="text-sm font-semibold uppercase tracking-wider text-slate-500">Active models</h4>
        <span className="text-xs text-slate-400">resolved per stage · click a row to edit</span>
      </div>
      <ul className="space-y-1.5">
        {STAGES.map(({ key, label }) => {
          const { providerName, model, provider, inherits } = resolveStage(routing, key);
          const reachRow = reachByStage[key];
          const liveStatus = !reachRow ? 'unknown' : reachRow.reachable ? 'up' : 'down';
          return (
            <li key={key}>
              <button
                type="button"
                onClick={onEdit}
                title="Edit providers & routing"
                className="w-full text-left flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-slate-200 bg-white/60 px-3 py-2 hover:bg-slate-50 transition-colors"
              >
                <LiveDot status={liveStatus} />
                <span className="text-sm font-semibold text-slate-700 w-24">{label}</span>
                <span className="text-sm text-slate-800 font-mono">
                  {providerName || <span className="text-rose-600">unset</span>}
                  {model ? ` · ${model}` : ''}
                </span>
                {inherits && (
                  <span className="text-xs text-slate-400 italic">inherits default</span>
                )}
                <span className="ml-auto flex items-center gap-1.5">
                  <ActionBadge tone="violet">{thinkingLabel(provider)}</ActionBadge>
                  <ActionBadge tone="slate">{tempLabel(provider)}</ActionBadge>
                </span>
              </button>
              {liveStatus === 'down' && reachRow?.detail && (
                <p className="text-xs text-rose-600 mt-0.5 ml-5">{reachRow.detail}</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
