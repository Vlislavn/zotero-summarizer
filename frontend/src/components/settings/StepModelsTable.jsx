// "Which model does each step, how fast, when last worked, does it work now" — one table.
// Facts come from existing sources: saved routing, run logs (/api/llm/step-history),
// the cheap provider check (/api/admin/llm-reachability) and the Test result.
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchProviderStatus, fetchStepHistory } from '../../api/stepModelsApi.js';
import { isLocalProvider, resolveStage } from '../../utils/configForm.js';
import { CONNECTION_STAGES, connectionLabel, connectionTaskLabel, providerLabel } from '../../utils/connectionTaskLabels.js';
import { formatDuration, formatWhen, testRowFor, testVerdict, tidyReason } from '../../utils/stepModelStatus.js';
import { Disclosure } from '../paper/review/primitives.jsx';
import Button from '../ui/Button.jsx';

const TONE = { ok: 'text-emerald-800', fail: 'text-rose-700', warn: 'text-amber-800', neutral: 'text-slate-600' };
const MARK = { ok: '✓', fail: '✗', warn: '!', neutral: '–' };

function Status({ tone, text, detail }) {
  return <div className={`text-sm ${TONE[tone]}`}>
    <p className="font-medium"><span aria-hidden>{MARK[tone]} </span>{text}</p>
    {detail && <p className="text-xs text-slate-600 break-words [overflow-wrap:anywhere]">{detail}</p>}
  </div>;
}

function providerStatus(reach, row) {
  if (reach.isPending || reach.isFetching) return { tone: 'neutral', text: 'Checking provider…' };
  if (reach.isError || !row) return { tone: 'neutral', text: 'Provider status unavailable' };
  if (row.status === 'needs_approval') return { tone: 'warn', text: 'Needs approval' };
  if (row.reachable) return { tone: 'ok', text: 'Provider reachable' };
  return { tone: 'fail', text: 'Not reachable' };
}

function speedText(history) {
  if (!history) return 'Speed unknown';
  if (!history.runs) return history.unit === 'per item' ? 'No timing recorded' : 'No runs recorded yet';
  return `Median ${formatDuration(history.median_seconds)} ${history.unit} · last success ${formatWhen(history.last_success_at)}`;
}

export default function StepModelsTable({ routing, test, testing = false, onChange, onTest, disabled, testDisabled, showFallback = false, summary = false }) {
  const [active, setActive] = useState(null);
  const reach = useQuery({ queryKey: ['llm-reachability'], queryFn: fetchProviderStatus, staleTime: 30_000, retry: false });
  const history = useQuery({ queryKey: ['llm-step-history'], queryFn: fetchStepHistory, staleTime: 30_000, retry: false });
  if (!routing) return null;
  const reachRows = reach.data?.stages || [];
  const rows = (summary ? ['default'] : [...CONNECTION_STAGES, ...(showFallback ? ['default'] : [])]).map(target => {
    const selected = resolveStage(routing, target);
    return { target, selected, reachRow: reachRows.find(row => row.stage === target) || reachRows.find(row => row.provider === selected.provider?.name) };
  });
  // Each provider once: its address, and (when the check failed) why it cannot be reached.
  const providers = [...new Map(rows.filter(row => row.selected.provider).map(({ selected, reachRow }) =>
    [selected.provider.name, { provider: selected.provider, down: reachRow && !reachRow.reachable && reachRow.status !== 'needs_approval' ? reachRow : null }])).values()];
  return <section aria-label="Model for each step" className="space-y-2">
    <ul className="divide-y divide-slate-200">
      {rows.map(({ target, selected, reachRow }) => {
        const verdict = test?.current === false ? null : testVerdict(testRowFor(test, target));
        const status = verdict ? { tone: verdict.tone, text: verdict.headline, detail: verdict.detail } : providerStatus(reach, reachRow);
        const label = summary ? 'All steps' : connectionTaskLabel(target);
        return <li key={target} aria-label={label} className="py-3 min-w-0">
          <div className={`grid gap-x-4 gap-y-1 items-start ${summary ? '' : 'md:grid-cols-[11rem_minmax(0,1fr)_minmax(0,1fr)_auto]'}`}>
            <p className="text-sm font-medium">{label}</p>
            <div className="min-w-0">
              <p className="text-sm break-words">{selected.model || 'No model selected'}</p>
              <p className="text-xs text-slate-600 [overflow-wrap:anywhere]">{providerLabel(selected.provider)}</p>
            </div>
            <div className="min-w-0 space-y-1">
              <p className="text-xs text-slate-600">{target === 'default' ? (summary ? 'Used by every step unless you set one under Advanced' : 'Used by steps without a model of their own') : speedText(history.data?.steps?.[target])}</p>
              <Status {...status} />
              {testing && active === target && <p role="status" className="text-xs text-slate-600">Sending one small prompt… up to {isLocalProvider(selected.provider) ? 60 : 30} s</p>}
            </div>
            {(onChange || onTest) && <div className="flex flex-wrap gap-2">
              {onChange && <Button variant="secondary" disabled={disabled} onClick={() => onChange(selected.provider, target)}
                aria-label={`Change ${label}`}>Change</Button>}
              {onTest && <Button variant="ghost" disabled={testDisabled || !selected.model}
                onClick={() => { setActive(target); onTest(target === 'default' ? undefined : [target]); }}
                aria-label={target === 'default' ? 'Test saved model' : `Test ${label}`}>
                {target === 'default' ? 'Test saved model' : 'Test'}
              </Button>}
            </div>}
          </div>
        </li>;
      })}
    </ul>
    {test?.current === false && <p role="status" className="text-sm text-amber-800">Test belongs to an older saved selection. Settings changed after it ran; test again.</p>}
    {(test?.ownership === 'unresolved' || test?.hold || test?.attempt?.hold) && <p role="status" className="text-sm text-amber-800">The request may still be running. Do not retry automatically; closing does not cancel it.</p>}
    {/* The read-only Settings overview states a failure once, in its Needs-attention strip. */}
    {(onChange || onTest) && providers.filter(entry => entry.down).map(({ provider, down }) =>
      <p key={provider.name} className="text-sm text-rose-700 [overflow-wrap:anywhere]"><span aria-hidden>✗ </span>{providerLabel(provider).split(' · ')[0]}: cannot connect to {down.base_url || provider.base_url || 'the provider'}{tidyReason(down.detail) && ` — ${tidyReason(down.detail)}`}</p>)}
    <div className="[&_summary]:min-h-11"><Disclosure summary="Providers and status checks">
      <ul className="space-y-1 text-xs text-slate-600 [overflow-wrap:anywhere]">{providers.map(({ provider }) => <li key={provider.name}>{connectionLabel(provider)}</li>)}</ul>
      <p className="mt-2 text-xs text-slate-600">Provider status asks the provider for its model list (no prompt, no cost). Test sends one small prompt to that step's model. Speed is the median of your recent runs.</p>
    </Disclosure></div>
  </section>;
}
