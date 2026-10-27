import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { researchFeedApi } from '../api/researchFeedApi.js';
import Async from '../components/ui/Async.jsx';
import Button from '../components/ui/Button.jsx';
import WeeklyDigest from '../components/WeeklyDigest.jsx';
import TelegramDigest from '../components/TelegramDigest.jsx';

export default function Weekly() {
  const [params, setParams] = useSearchParams();
  const runId = params.get('run') || '';
  const runs = useQuery({ queryKey: ['weekly-runs'], queryFn: researchFeedApi.list, retry: false, networkMode: 'always' });
  const report = useQuery({ queryKey: ['weekly-report', runId], queryFn: () => researchFeedApi.get(runId),
    enabled: Boolean(runId), retry: false, networkMode: 'always' });
  return <div className="space-y-5 min-w-0">
    <header><h1 className="font-display text-3xl text-forest-800">Weekly Research Intelligence</h1>
      <p>Browse saved RSS research digests and Telegram conversation mini-reviews.</p>
      <p className="text-sm text-slate-600">Read-only snapshots: opening a digest makes no AI calls, paper acquisition or Zotero writes. Reports need the local server; they are not cached for offline use.</p>
    </header>
    <section aria-label="Saved weekly digests" className="space-y-3">
      <div className="flex flex-wrap gap-3 items-end"><label className="min-w-0 max-w-full">Saved digest
        <select className="block border rounded-lg p-2 w-full max-w-full" value={runId} onChange={(event) => setParams(event.target.value ? { run: event.target.value } : {})}>
          <option value="">Choose a saved run</option>
          {runId && !runs.data?.items?.some((item) => item.run_id === runId) && <option value={runId}>{runId}</option>}
          {(runs.data?.items || []).map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id}{item.available ? ` · ${item.counts.cards_generated} cards${item.source === 'telegram' ? ' · Telegram' : ''}` : ' · unavailable'}</option>)}
        </select>
      </label><Button variant="secondary" onClick={() => { runs.refetch(); if (runId) report.refetch(); }}>Refresh saved runs</Button></div>
      <p className="text-sm text-slate-600">Up to 100 saved runs, newest publication first across all sources. Unavailable or undated bundles use their saved filesystem time. Bookmark the selected URL to reopen any retained digest.</p>
      <Async loading={runs.isPending} error={runs.error} empty={runs.data?.items?.length === 0} emptyMessage="No saved weekly digests yet. Generate one with the existing research-feed CLI, then refresh saved runs." />
    </section>
    {runId && <Async loading={report.isPending} error={report.error}>
      {report.data && <><div className="flex flex-wrap gap-4 text-sm">
        <a className="underline" href={researchFeedApi.download(runId, 'json')}>Download canonical JSON</a>
        <a className="underline" href={researchFeedApi.download(runId, 'md')}>Download Markdown</a>
      </div>{report.data.metadata.source === 'telegram' ? <TelegramDigest report={report.data} /> : <WeeklyDigest report={report.data} />}</>}
    </Async>}
  </div>;
}
