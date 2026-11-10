import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { TelegramSourcesTask } from '../components/settings/SourceTasks.jsx';
import { taskUrl } from '../utils/settingsNavigation.js';
import { researchFeedApi } from '../api/researchFeedApi.js';
import Async from '../components/ui/Async.jsx';
import Button from '../components/ui/Button.jsx';
import WeeklyDigest from '../components/WeeklyDigest.jsx';
import TelegramHistory from '../components/TelegramHistory.jsx';
import TelegramToday from '../components/TelegramToday.jsx';

export default function Weekly() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const task = params.get('task');
  const runId = params.get('run') || '';
  useEffect(() => {
    if (!task && location.state?.focusTask) document.getElementById(`task-${location.state.focusTask}`)?.focus();
  }, [task, location.state?.focusTask]);
  const runs = useQuery({ queryKey: ['weekly-runs'], queryFn: researchFeedApi.list, enabled: !task, retry: false, networkMode: 'always' });
  const report = useQuery({ queryKey: ['weekly-report', runId], queryFn: () => researchFeedApi.get(runId),
    enabled: Boolean(runId) && !task, retry: false, networkMode: 'always' });
  if (params.get('past')) return <TelegramHistory runId={params.get('past')} />;
  if (task === 'telegram') return <TelegramSourcesTask key={task} />;
  if (task) return <section role="alert"><h1>Weekly task not found</h1><Link to={taskUrl(location, null)}>Back to Weekly</Link></section>;
  const items = runs.data?.items || [];
  return <div className="mx-auto w-full min-w-0 max-w-3xl space-y-6">
    <TelegramToday headerAction={<>
      <Link id="task-telegram" to={taskUrl(location, 'telegram')} className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-4 sm:min-h-8">Sources</Link>
      <span aria-hidden="true">·</span>
      <Link to={{ search: '?past=list' }} className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-4 sm:min-h-8">Past digests</Link>
    </>} />
    {(items.length > 0 || runId || runs.error) && <details open={Boolean(runId)} aria-label="Saved weekly digests" className="max-w-3xl border-t border-slate-200 pt-1">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 font-display text-xl font-light text-forest-800">
        Saved digests<span className="font-sans text-sm text-slate-600">{items.length || ''}</span>
      </summary>
      <div className="flex flex-wrap items-end gap-3 pb-3"><label className="min-w-0 max-w-full text-sm text-slate-700">Saved digest
        <select className="mt-1 block min-h-11 w-full max-w-full rounded-lg border border-slate-300 bg-white p-2 text-sm" value={runId} onChange={(event) => setParams(previous => {
          const next = new URLSearchParams(previous);
          if (event.target.value) next.set('run', event.target.value); else next.delete('run');
          return next;
        })}>
          <option value="">Choose a saved run</option>
          {runId && !items.some((item) => item.run_id === runId) && <option value={runId}>{runId}</option>}
          {items.map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id}{item.available ? ` · ${item.counts.cards_generated} cards${item.source === 'telegram' ? ' · Telegram' : ''}` : ` · unavailable — ${item.reason || 'cannot be opened'}`}</option>)}
        </select>
      </label><Button variant="secondary" className="min-h-11" onClick={() => { runs.refetch(); if (runId) report.refetch(); }}>Refresh saved runs</Button></div>
      <Async error={runs.error} />
      {(runs.error || report.error) && <p className="pb-2 text-xs text-slate-600">Saved reports need the local server and are not cached for offline use.</p>}
    </details>}
    {runId && <Async loading={report.isPending} error={report.error}>
      {report.data && <><div className="flex flex-wrap gap-4 text-sm">
        <a className="underline" href={researchFeedApi.download(runId, 'json')}>Download canonical JSON</a>
        <a className="underline" href={researchFeedApi.download(runId, 'md')}>Download Markdown</a>
      </div><WeeklyDigest report={report.data} /></>}
    </Async>}
  </div>;
}
