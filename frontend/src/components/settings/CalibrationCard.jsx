import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { calibrateSetup, fetchCalibrateStatus } from '../../api/settingsApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, SectionCard } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const POLL_MS = 2500;
const START_KEY = ['setup-calibration-start'];
const STATUSES = ['idle', 'not_started', 'running', 'ready', 'error'];
function observedStatus(value) {
  if (!STATUSES.includes(value?.status)) throw new Error('Calibration status is unavailable. Read status before starting another run.');
  return value;
}

export default function CalibrationCard() {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['setup-calibration'], queryFn: async () => observedStatus(await fetchCalibrateStatus()), retry: false,
    refetchOnMount: 'always', refetchInterval: current => !current.state.error && current.state.data?.status === 'running' ? POLL_MS : false });
  const pendingStart = useIsMutating({ mutationKey: START_KEY }) > 0;
  const mutation = useMutation({ mutationKey: START_KEY, mutationFn: async () => observedStatus(await calibrateSetup({ papers: 3 })),
    onSuccess: value => cache.setQueryData(['setup-calibration'], value),
    onError: () => cache.invalidateQueries({ queryKey: ['setup-calibration'] }) });
  const job = query.data;
  const running = pendingStart || job?.status === 'running';
  const result = job?.status === 'ready' ? job.result : null;
  const t2 = result?.tier2;
  const total = job?.total || 0;
  const progressLabel = pendingStart ? 'Starting calibration…' : running ? total ? `Calibrating… reviewed ${job.completed}/${total}` : 'Calibrating… measuring endpoint' : 'Start review calibration';
  const changed = result ? Object.keys(t2?.entries || {}).length || result.tier1?.profile === 'lean'
    ? `text budget ${t2?.winner_max_text_chars} chars · ${result.tier1?.profile} profile`
    : 'no tuning change reported; existing defaults retained' : '';
  return <SectionCard title="Review calibration"
    description="Estimates text budget and lean/full profile for the saved deep-review endpoint using bounded real reviews (3 papers × 2 budgets). Needs an existing paper brief. Results are saved to calibration.json and applied on the next config load; this is not a guarantee of scientific completeness or local hardware safety.">
    <div className="space-y-4">
      {query.isPending && <p role="status">Reading current calibration status…</p>}
      {query.isError && <Banner kind="error">Status unavailable. Server work may still be running; starting another run is blocked. {humanizeError(query.error)}</Banner>}
      {mutation.isError && <Banner kind="error">Calibration request failed: {humanizeError(mutation.error)} No automatic restart.</Banner>}
      {!query.isError && job?.status === 'error' && <Banner kind="error">Calibration failed: {job.error || 'No further failure detail was returned.'}</Banner>}
      {!query.isError && running && <p role="status" className="text-sm">{progressLabel}{job?.phase ? ` · ${job.phase}` : ''}. Leaving this view does not cancel server work.</p>}
      {!query.isError && result && <p role="status" className="text-sm text-emerald-700">Last completed calibration: {result.provider}/{result.model} · {result.papers} paper(s) · {changed}</p>}
      <div className="flex flex-wrap gap-3">
        <Button type="button" disabled={running || mutation.isError || query.isPending || query.isError || query.isFetching} onClick={() => mutation.mutate()}>{progressLabel}</Button>
        <Button type="button" variant="secondary" disabled={query.isFetching || pendingStart} onClick={async () => { const read = await query.refetch(); if (!read.isError && mutation.isError) mutation.reset(); }}>Read calibration status</Button>
      </div>
    </div>
  </SectionCard>;
}
