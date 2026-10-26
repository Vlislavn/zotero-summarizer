import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchDoctorStatus, runDoctor, cancelDoctor } from '../../api/setupApi.js';
import { doctorQueryOptions, doctorVerificationOptions } from '../../utils/setupDoctorState.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, SectionCard } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const STATUS = {
  not_started: ['○', 'Not started', 'text-slate-500'],
  running: ['◌', 'Running', 'text-sky-700'],
  ready: ['✓', 'Ready', 'text-emerald-700'],
  needs_action: ['!', 'Needs action', 'text-amber-800'],
  unavailable: ['—', 'Unavailable', 'text-slate-500'],
};
const RECOVERY_DESTINATIONS = {
  open_ai_settings: '/settings#ai-models',
  open_sources_settings: '/settings#rss-feeds',
};

function DoctorCheck({ check, running, onRetry }) {
  const meta = STATUS[check.status] || STATUS.not_started;
  const destination = RECOVERY_DESTINATIONS[check.recovery?.action];
  return <li className="py-2 flex items-start gap-2">
    <span className={`${meta[2]} font-medium w-4`} aria-hidden>{meta[0]}</span>
    <div className="min-w-0 flex-1">
      <p className="text-xs text-slate-500">
        <span className={meta[2]}>{meta[1]}</span>{' · '}{check.id.replaceAll('_', ' ')}
      </p>
      <p className="text-sm text-slate-800">{check.message}</p>
      {check.load_receipts?.some((receipt) => receipt.admission?.status === 'unknown') &&
        <p className="text-sm text-amber-800">Local load safety is not established.</p>}
      {check.detail && <details className="text-xs text-slate-500 mt-1">
        <summary className="cursor-pointer">Technical details</summary>
        <p className="break-words mt-1">{check.detail}</p>
      </details>}
      {check.status === 'needs_action' && <div className="mt-1 flex flex-wrap gap-2 items-center">
        {destination && <a href={destination} className="text-teal-700 underline text-sm">
          {check.recovery?.label || 'Review settings'}
        </a>}
        {(!destination || check.recovery?.action === 'open_ai_settings') &&
          <Button type="button" variant="secondary" disabled={running} onClick={onRetry}>
            {destination ? 'Retry' : check.recovery?.label || 'Retry'}
          </Button>}
        {check.recovery?.command && <code className="text-xs select-all break-words">{check.recovery.command}</code>}
      </div>}
    </div>
  </li>;
}

export function DoctorChecklist() {
  const queryClient = useQueryClient();
  const query = useQuery({ ...doctorQueryOptions(queryClient, fetchDoctorStatus),
    refetchInterval: (current) => current.state.data?.status === 'running' ? 1000 : false });
  const mutation = useMutation(doctorVerificationOptions(queryClient, runDoctor));
  const cancellation = useMutation({ mutationFn: cancelDoctor,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['setup-doctor'] }) });

  if (query.isLoading) return <p className="text-sm text-slate-500">Loading local health…</p>;
  if (query.isError) return <Banner kind="error">{humanizeError(query.error)} Verification status is unknown;
    server work may still be running. Reload to retry the status check.</Banner>;
  const data = query.data;
  const running = mutation.isPending || data?.status === 'running';
  const checks = data?.checks || [];
  const pending = checks.filter((check) => check.status === 'not_started');
  const visible = checks.filter((check) => ['needs_action', 'running'].includes(check.status) ||
    (check.status === 'not_started' && pending.length === 1));
  const passed = checks.filter((check) => check.status === 'ready');
  const unavailable = checks.filter((check) => check.status === 'unavailable');
  const otherChecks = checks.filter((check) => ['ready', 'unavailable'].includes(check.status));
  const otherLabel = [passed.length && `${passed.length} ${passed.length === 1 ? 'check' : 'checks'} passed`,
    unavailable.length && `${unavailable.length} unavailable`].filter(Boolean).join(' · ');
  const showModes = Boolean(
    data?.modes?.local_inference && data.modes.local_inference !== 'unavailable',
  );
  const row = (check) => <DoctorCheck key={check.id} check={check} running={running}
    onRetry={() => mutation.mutate([check.id])} />;

  return (
    <div className="space-y-3">
      {showModes && <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" aria-label="Offline modes">
        {[
          ['local_inference', 'Local inference'],
          ['offline_ready', 'Offline-ready'],
          ['strict_offline', 'Strict offline'],
        ].map(([key, label]) => {
          const value = data?.modes?.[key] || 'not_started';
          const meta = STATUS[value] || STATUS.not_started;
          return <div key={key} className="rounded-lg border border-slate-200 p-2 text-xs">
            <span className={meta[2]} aria-hidden>{meta[0]} </span>
            <span className="font-semibold">{label}</span>
            <span className="block text-slate-500">{meta[1]}</span>
          </div>;
        })}
      </div>}

      {mutation.isPending && <p role="status" className="text-sm text-sky-700">
        Verification can take a few minutes. Results will appear here when complete.
      </p>}
      {!running && !query.isFetching && data?.ready && <p className="text-sm font-medium text-emerald-700">
        ✓ Setup verified
      </p>}
      {!mutation.isPending && !data?.ready && checks.length === 0 && (
        <p className="text-sm text-slate-500">Verification has not run yet.</p>
      )}

      {checks.length > 0 && <ul className="divide-y divide-slate-100" aria-label="Setup checks" aria-live="polite">
        {visible.map(row)}
        {pending.length > 1 && <li className="py-2">
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">{pending.length} checks not started</summary>
            <ul className="mt-1 divide-y divide-slate-100">{pending.map(row)}</ul>
          </details>
        </li>}
        {otherChecks.length > 0 && <li className="py-2">
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">{otherLabel}</summary>
            <ul className="mt-1 divide-y divide-slate-100">{otherChecks.map(row)}</ul>
          </details>
        </li>}
      </ul>}

      {(mutation.isError || cancellation.isError) && <Banner kind="error">{humanizeError(mutation.error || cancellation.error)}</Banner>}
      {data?.attempt?.ownership === 'unresolved' && <p role="status" className="text-sm text-amber-800">
        Verification stopped. Callback or remote termination is not established; no Ready result was accepted.
      </p>}
      <div className="flex flex-wrap gap-3 items-center">
        <Button type="button" variant="secondary" onClick={() => mutation.mutate(null)} disabled={running}>
          {running ? 'Verifying…' : 'Verify setup'}
        </Button>
        {running && data?.run_id && <Button type="button" variant="secondary" disabled={cancellation.isPending}
          onClick={() => cancellation.mutate(data.run_id)}>Stop verification</Button>}
        {data?.last_successful_at && <span className="text-xs text-slate-500">
          Last successful check: {new Date(data.last_successful_at).toLocaleString()}
        </span>}
      </div>
    </div>
  );
}

export default function DeploymentCard() {
  return <SectionCard title="Local setup health"
    description="One shared web/CLI checklist for runtime, models, Zotero, sources and a real no-write triage.">
    <DoctorChecklist />
  </SectionCard>;
}
