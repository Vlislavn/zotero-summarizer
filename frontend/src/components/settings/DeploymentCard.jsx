import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchDoctorStatus, runDoctor } from '../../api/setupApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { doctorQueryOptions, doctorVerificationOptions } from '../../utils/setupDoctorState.js';
import { Banner, SectionCard } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const DOCTOR_POLL_MS = 3000;
const STATUS = {
  not_started: ['○', 'Not started', 'text-slate-500'],
  running: ['◌', 'Running', 'text-sky-700'],
  ready: ['✓', 'Ready', 'text-emerald-700'],
  needs_action: ['!', 'Needs action', 'text-amber-800'],
  unavailable: ['—', 'Unavailable', 'text-slate-500'],
};
const SETTINGS_TARGETS = {
  open_ai_settings: 'ai-models',
  open_goals_settings: 'essentials-goals',
  open_zotero_settings: 'essentials-zotero-paths',
  open_sources_settings: 'rss-feeds',
};
const MODES = [
  ['local_inference', 'Local inference', 'Inference stays on this machine. RSS and PDF acquisition may still use the internet.'],
  ['offline_ready', 'Offline-ready', 'Required model assets load from cache without internet. Uncached papers still need a connection.'],
  ['strict_offline', 'Strict offline', 'When enabled, optional network enrichment and remote fallbacks are disabled.'],
];

function CheckRow({ check, busy, onRetry, onOpenSettings, onOpenAiSettings }) {
  const meta = STATUS[check.status] || STATUS.not_started;
  const target = SETTINGS_TARGETS[check.recovery?.action];
  const guidance = check.recovery?.label || 'Retry';
  const label = check.recovery?.action === 'retry' && !guidance.startsWith('Retry')
    ? 'Retry check' : guidance;
  return <li className="py-2 flex items-start gap-2">
    <span className={`${meta[2]} w-4 shrink-0`} aria-hidden>{meta[0]}</span>
    <div className="min-w-0 flex-1 space-y-1">
      <p className="text-sm text-slate-800 break-words">{check.message}</p>
      <p className={`text-xs ${meta[2]}`}>{meta[1]}</p>
      {check.load_receipts?.some((receipt) => receipt.admission?.status === 'unknown') && <p className="text-xs text-slate-600">
        Local load safety is not established for this configuration. A listed model or a download is not verification; Full local setup remains incomplete.
      </p>}
      {check.detail && <details className="text-xs text-slate-500">
        <summary className="cursor-pointer focus-visible:outline focus-visible:outline-2">Technical details</summary>
        <p className="break-words mt-1 [overflow-wrap:anywhere]">{check.detail}</p>
      </details>}
      {check.status === 'needs_action' && <div className="flex flex-wrap gap-2 items-center">
        {target && <Button type="button" variant="secondary" disabled={busy}
          onClick={() => target === 'ai-models' && onOpenAiSettings
            ? onOpenAiSettings() : onOpenSettings?.(target)}>
          {guidance}
        </Button>}
        <Button type="button" variant="secondary" disabled={busy} onClick={onRetry}>
          {target ? 'Retry check' : label}
        </Button>
        {!target && label !== guidance && <span className="text-xs text-slate-600">{guidance}</span>}
        {check.recovery?.command && <code className="text-xs select-all basis-full [overflow-wrap:anywhere]">
          {check.recovery.command}
        </code>}
      </div>}
    </div>
  </li>;
}

export function DoctorChecklist({ onOpenAiSettings, onOpenSettings, expanded = false }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    ...doctorQueryOptions(queryClient, fetchDoctorStatus),
    refetchInterval: (current) => current.state.status !== 'error' && current.state.data?.status === 'running'
      ? DOCTOR_POLL_MS : false,
  });
  const mutation = useMutation(doctorVerificationOptions(queryClient, runDoctor));

  if (query.isLoading) return <p role="status" className="text-sm text-slate-500">Loading local health…</p>;
  if (query.isError) return <div className="space-y-2">
    <Banner kind="error">Could not read setup health: {humanizeError(query.error)}</Banner>
    <Button variant="secondary" disabled={query.isFetching} onClick={() => query.refetch()}>Retry status</Button>
  </div>;
  const data = query.data;
  const busy = mutation.isPending || data?.status === 'running';
  const checks = data?.checks || [];
  const passed = checks.filter((check) => check.status === 'ready').length;
  const activeProfile = checks.find((check) => check.id === 'local_profile');

  return (
    <div className="space-y-3">
      {activeProfile && activeProfile.status !== 'not_started' && <p className="text-sm text-slate-700">
        {activeProfile.message}
      </p>}
      {Object.keys(data?.modes || {}).length > 0 && <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" aria-label="Offline modes">
        {MODES.map(([key, label, explanation]) => {
          const meta = STATUS[data.modes[key]] || STATUS.not_started;
          return <div key={key} className="rounded-lg border border-slate-200 p-2 text-xs space-y-1">
            <p><span className={meta[2]} aria-hidden>{meta[0]} </span><span className="font-medium">{label}</span></p>
            <p className={meta[2]}>{meta[1]}</p>
            <p className="text-slate-600">{explanation}</p>
          </div>;
        })}
      </div>}

      {busy && <p role="status" className="text-sm text-sky-700">
        Verification can take a few minutes. Results will appear here when complete.
      </p>}
      {!busy && data?.ready && <p role="status" className="text-sm font-medium text-emerald-700">✓ Setup verified</p>}
      {!busy && !data?.ready && (!data?.status || data.status === 'not_started')
        && checks.every((check) => check.status === 'not_started') && (
        <p className="text-sm text-slate-500">Verification has not run yet.</p>
      )}

      {checks.length > 0 && <details open={expanded || !data?.ready} className="text-sm text-slate-600">
        <summary className="cursor-pointer focus-visible:outline focus-visible:outline-2">
          {passed} of {checks.length} checks ready
        </summary>
        <ol className="divide-y divide-slate-100 mt-2" aria-label="Setup checks" aria-live="polite">
          {checks.map((check) => <CheckRow key={check.id} check={check} busy={busy}
            onRetry={() => mutation.mutate([check.id])}
            onOpenAiSettings={onOpenAiSettings} onOpenSettings={onOpenSettings} />)}
        </ol>
      </details>}

      {mutation.isError && <Banner kind="error">Verification request failed: {humanizeError(mutation.error)}. Read status before retrying; server work may still be running.</Banner>}
      <div className="flex flex-wrap gap-3 items-center">
        <Button type="button" variant="secondary" onClick={() => mutation.mutate(null)} disabled={busy}>
          {busy ? 'Verifying…' : 'Verify setup'}
        </Button>
        {mutation.isError && <Button variant="secondary" onClick={() => query.refetch()} disabled={query.isFetching}>Read status</Button>}
        {data?.last_successful_at && <span className="text-xs text-slate-500">
          Last successful check: {new Date(data.last_successful_at).toLocaleString()}
        </span>}
      </div>
      <p className="text-xs text-slate-500">Checks run only when requested. They may load cached ML assets and send real requests to configured models; they never write to Zotero.</p>
    </div>
  );
}

export default function DeploymentCard({ onOpenAiSettings, onOpenSettings, children }) {
  return <SectionCard title="Local setup health"
    description="One shared web/CLI checklist for runtime, models, Zotero, sources and a real no-write triage.">
    <div className="flex flex-wrap justify-end mb-2">
      <Button variant="secondary" onClick={onOpenAiSettings}>Change profile</Button>
    </div>
    <DoctorChecklist onOpenAiSettings={onOpenAiSettings} onOpenSettings={onOpenSettings} />
    {children && <details className="mt-3 text-xs text-slate-600">
      <summary className="cursor-pointer">Technical details · startup subsystems</summary>
      <p className="my-2">Startup reachability and classifier state are diagnostics, not end-to-end verification.</p>
      {children}
    </details>}
  </SectionCard>;
}
