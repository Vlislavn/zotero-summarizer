import { useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { conferencesApi } from '../../api/conferencesApi.js';
import Async from '../ui/Async.jsx';
import Button from '../ui/Button.jsx';
import ImportBar from './ImportBar.jsx';
import { RowList } from './ConferenceRows.jsx';

const fmt = (n) => n.toLocaleString('en-GB');
const when = (iso) => new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });

// The only control that reaches the network: Crossref is asked after this click, never on page load.
function UpdateBar({ id, year }) {
  const client = useQueryClient();
  const preflight = useQuery({ queryKey: ['conference-preflight', id, year], queryFn: () => conferencesApi.preflight(id, year), retry: false, networkMode: 'always' });
  const refresh = useMutation({ mutationFn: () => conferencesApi.refresh(id, year),
    onSettled: () => { client.invalidateQueries({ queryKey: ['conferences'] }); client.invalidateQueries({ queryKey: ['conference', id, year] }); } });
  const p = preflight.data;
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <Button className="min-h-11" disabled={!p || p.offline || refresh.isPending} onClick={() => refresh.mutate()}>{refresh.isPending ? 'Updating…' : 'Update'}</Button>
      <p id="conference-preflight" className="text-sm text-slate-700">
        {p ? (p.offline ? 'Offline mode is on' : `Reads ${p.source} · ≈${p.requests} requests · ${p.login ? 'login needed' : 'no login'}`) : ' '}
      </p>
    </div>
    <Async error={preflight.error || refresh.error} />
    {refresh.data && <p role={refresh.data.ok ? 'status' : 'alert'} className={`text-sm ${refresh.data.ok ? 'text-slate-700' : 'rounded-lg border border-amber-200 bg-amber-50 p-2 text-amber-800'}`}>{refresh.data.message}</p>}
  </div>;
}

function Pager({ page, pages, onPage }) {
  return <div className="flex items-center gap-3 py-2 text-sm text-slate-700">
    <Button variant="secondary" className="min-h-11" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
    <span>Page {page} of {fmt(pages)}</span>
    <Button variant="secondary" className="min-h-11" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
  </div>;
}

function Lists({ id, year, titleOnly }) {
  const [page, setPage] = useState(1);
  const [draft, setDraft] = useState('');
  const [q, setQ] = useState('');
  const view = useQuery({ queryKey: ['conference', id, year, page, q], queryFn: () => conferencesApi.view(id, year, { page, q }),
    placeholderData: keepPreviousData, retry: false, networkMode: 'always' });
  const d = view.data;
  if (!d?.downloaded) return <Async loading={view.isPending} error={view.error}><p className="text-sm text-slate-700">Press Update to read the list.</p></Async>;
  const { counts } = d;
  return <div className="space-y-4">
    <div className="text-sm text-slate-700">
      <p>{fmt(counts.total)} abstracts · {fmt(counts.late_breaking)} late-breaking · {fmt(counts.unknown)} level unknown{d.skipped > 0 && ` · ${fmt(d.skipped)} other entries skipped`}</p>
      <p className="text-xs text-slate-600">Only late-breaking can be told from free metadata.</p>
    </div>
    <form className="flex flex-wrap gap-2" role="search" onSubmit={(event) => { event.preventDefault(); setPage(1); setQ(draft.trim()); }}>
      <label className="sr-only" htmlFor="conference-title-search">Search titles</label>
      <input id="conference-title-search" type="search" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Search titles"
        className="min-h-11 min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 text-sm" />
      <Button type="submit" variant="secondary" className="min-h-11">Search</Button>
    </form>
    <section aria-labelledby="conference-late">
      <h2 id="conference-late" className="font-display text-xl font-light text-forest-800">Late-breaking ({fmt(d.late_breaking.length)}{q ? ` of ${fmt(counts.late_breaking)}` : ''})</h2>
      {d.late_breaking.length ? <RowList rows={d.late_breaking} id={id} year={year} titleOnly={titleOnly} /> : <p className="py-2 text-sm text-slate-600">No late-breaking abstract{q ? ' matches' : ' in this list'}.</p>}
    </section>
    <section aria-labelledby="conference-all">
      <h2 id="conference-all" className="font-display text-xl font-light text-forest-800">All abstracts ({fmt(d.matching)}{q ? ` of ${fmt(counts.total)}` : ''})</h2>
      <RowList rows={d.items} id={id} year={year} titleOnly={titleOnly} />
      <Pager page={d.page} pages={d.pages} onPage={setPage} />
    </section>
  </div>;
}

export default function EditionPanel({ conference, edition }) {
  const { id } = conference;
  return <div className="space-y-4">
    <p className="text-sm text-slate-700">
      {edition.downloaded_at ? `Downloaded ${when(edition.downloaded_at)} · ${fmt(edition.count)} abstracts` : 'Not downloaded yet'}
      {' · '}window {edition.from} to {edition.until}
      {!edition.abstract_available && <span className="ml-2 whitespace-nowrap rounded-full border border-amber-700 px-2 text-xs text-amber-800">title only</span>}
    </p>
    {edition.partial && <p className="text-sm text-amber-800">An interrupted download ({fmt(edition.partial.count)} records, {when(edition.partial.saved_at)}) is saved apart and is not shown as a list.</p>}
    <UpdateBar id={id} year={edition.year} />
    <ImportBar id={id} year={edition.year} />
    <Lists key={`${id}/${edition.year}`} id={id} year={edition.year} titleOnly={!edition.abstract_available} />
  </div>;
}
