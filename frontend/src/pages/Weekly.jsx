import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { researchFeedApi } from '../api/researchFeedApi.js';
import Async from '../components/ui/Async.jsx';
import Button from '../components/ui/Button.jsx';
import WeeklyDigest from '../components/WeeklyDigest.jsx';

function initialWindow() {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 6);
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10), venue: '', card_budget: 10 };
}

export default function Weekly() {
  const [params, setParams] = useSearchParams();
  const runId = params.get('run') || '';
  const [range, setRange] = useState(initialWindow);
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ['weekly-runs'], queryFn: researchFeedApi.list, retry: false });
  const report = useQuery({ queryKey: ['weekly-report', runId], queryFn: () => researchFeedApi.get(runId), enabled: Boolean(runId), retry: false });
  const run = useMutation({ mutationFn: researchFeedApi.run, onSuccess: (result) => {
    queryClient.invalidateQueries({ queryKey: ['weekly-runs'] });
    setParams({ run: result.run_id });
  } });
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, []);
  const offline = !online;
  const venueArgument = range.venue ? ` --venue '${range.venue.replaceAll("'", "'\"'\"'")}'` : '';
  function update(event) {
    const { name, value } = event.target;
    setRange((previous) => ({ ...previous, [name]: name === 'card_budget' ? Number(value) : value }));
  }
  return <div className="space-y-5 min-w-0">
    <header><h1 className="font-display text-3xl text-forest-800">Weekly Research Intelligence</h1>
      <p>Review a small project-specific digest from your existing RSS triage and full-text reviews.</p>
      <p className="text-sm text-slate-600">Building here uses saved reviews only: no new AI calls, downloads or Zotero writes. This page needs the local server.</p>
    </header>
    <form onSubmit={(event) => { event.preventDefault(); if (!run.isPending && navigator.onLine) run.mutate(range); }} className="rounded-2xl border border-slate-200 p-4 space-y-3">
      <div className="flex flex-wrap gap-3">
        <label>From (UTC)<input required type="date" name="start" value={range.start} onChange={update} className="block border rounded-lg p-2" /></label>
        <label>To (inclusive, UTC)<input required type="date" name="end" min={range.start} value={range.end} onChange={update} className="block border rounded-lg p-2" /></label>
        <label>Venue (optional)<input name="venue" maxLength={200} value={range.venue} onChange={update} className="block border rounded-lg p-2" /></label>
        <label>Maximum cards<input required type="number" name="card_budget" min={1} max={10} value={range.card_budget} onChange={update} className="block border rounded-lg p-2 w-24" /></label>
      </div>
      <Button type="submit" disabled={run.isPending || offline}>{run.isPending ? 'Building from saved reviews…' : 'Build from saved reviews'}</Button>
      {offline && <p role="status">Offline: weekly reports are not cached on this device. Reconnect to the local server.</p>}
      {run.isError && <div role="alert"><p>{run.error.message}</p><p>No completion was confirmed. Refresh saved runs before retrying; a disconnected request may still have published a report.</p></div>}
    </form>
    <details><summary className="cursor-pointer">Need new papers or full-text cards?</summary>
      <p>First <Link className="underline" to="/today">refresh and triage sources in Today</Link>. Open missing paper reviews below, or explicitly generate through the same weekly CLI service:</p>
      <pre className="whitespace-pre-wrap break-all text-sm p-2">zotero-summarizer research-feed run --from {range.start}T00:00:00Z --to {range.end}T23:59:59Z --card-budget {range.card_budget}{venueArgument}</pre>
      <p>That command may acquire papers and use your configured AI provider. Zotero remains unchanged unless you explicitly pass --queue-zotero. Refresh saved runs afterwards.</p>
    </details>
    <section aria-label="Saved weekly digests" className="space-y-3">
      <div className="flex flex-wrap gap-3 items-end"><label className="min-w-0 max-w-full">Saved digest
        <select className="block border rounded-lg p-2 w-full max-w-full" value={runId} onChange={(event) => setParams(event.target.value ? { run: event.target.value } : {})}>
          <option value="">Choose a saved run</option>
          {runId && !runs.data?.items?.some((item) => item.run_id === runId) && <option value={runId}>{runId}</option>}
          {(runs.data?.items || []).map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id}{item.available ? ` · ${item.counts.cards_generated} cards` : ' · unavailable'}</option>)}
        </select>
      </label><Button variant="secondary" onClick={() => { runs.refetch(); if (runId) report.refetch(); }}>Refresh saved runs</Button></div>
      <Async loading={runs.isPending} error={runs.error} empty={runs.data?.items?.length === 0} emptyMessage="No saved weekly digests yet. Build a bounded run above." />
    </section>
    {runId && <Async loading={report.isPending} error={report.error}>
      {report.data && <><div className="flex flex-wrap gap-4 text-sm">
        <a className="underline" href={researchFeedApi.download(runId, 'json')}>Download canonical JSON</a>
        <a className="underline" href={researchFeedApi.download(runId, 'md')}>Download Markdown</a>
      </div><WeeklyDigest report={report.data} /></>}
    </Async>}
  </div>;
}
