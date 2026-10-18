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
  const visible = checks.filter((check) => ['needs_action', 'running'].includes(check.status));
  const passed = checks.filter((check) => check.status === 'ready');
  const showModes = Boolean(
    data?.modes?.local_inference && data.modes.local_inference !== 'unavailable',
  );

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
      {!mutation.isPending && !data?.ready && visible.length === 0 && (
        <p className="text-sm text-slate-500">Verification has not run yet.</p>
      )}

      {visible.length > 0 && <ul className="divide-y divide-slate-100" aria-live="polite">
        {visible.map((check) => {
          const meta = STATUS[check.status] || STATUS.not_started;
          return <li key={check.id} className="py-2 flex items-start gap-2">
            <span className={`${meta[2]} font-medium w-4`} aria-hidden>{meta[0]}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-800">
                <span className="sr-only">{meta[1]}: </span>{check.message}
              </p>
              {check.detail && <details className="text-xs text-slate-500 mt-1">
                <summary className="cursor-pointer">Technical details</summary>
                <p className="break-words mt-1">{check.detail}</p>
              </details>}
              {check.status === 'needs_action' && <div className="mt-1 flex flex-wrap gap-2 items-center">
                {check.recovery?.action === 'open_ai_settings' && <a href="/settings" className="text-teal-700 underline text-sm">
                  Review AI settings (Hosted or No LLM)
                </a>}
                <Button type="button" variant="secondary" disabled={running}
                  onClick={() => mutation.mutate([check.id])}>
                  {check.recovery?.action === 'open_ai_settings' ? 'Retry' : check.recovery?.label || 'Retry'}
                </Button>
                {check.recovery?.command && <code className="text-xs select-all">{check.recovery.command}</code>}
              </div>}
            </div>
          </li>;
        })}
      </ul>}

      {passed.length > 0 && <details className="text-xs text-slate-500">
        <summary className="cursor-pointer">
          {passed.length} {passed.length === 1 ? 'check' : 'checks'} passed
        </summary>
        <ul className="mt-1 pl-5 list-disc space-y-1">
          {passed.map((check) => <li key={check.id}>{check.message}</li>)}
        </ul>
      </details>}

      {(mutation.isError || cancellation.isError) && <Banner kind="error">{humanizeError(mutation.error || cancellation.error)}</Banner>}
      {data?.attempt?.ownership === 'unresolved' && <p role="status" className="text-sm text-amber-800">
        Verification stopped. Callback or remote termination is not established; no Ready result was accepted.
      </p>}
      <div className="flex flex-wrap gap-3 items-center">
        <Button type="button" variant="secondary" onClick={() => mutation.mutate(null)} disabled={running}>
          {mutation.isPending ? 'Verifying…' : 'Verify setup'}
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
