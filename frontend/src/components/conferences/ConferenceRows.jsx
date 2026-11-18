import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { conferencesApi } from '../../api/conferencesApi.js';
import Async from '../ui/Async.jsx';

function authorLine(authors) {
  if (!authors.length) return '';
  return authors.length > 3 ? `${authors.slice(0, 3).join(', ')} +${authors.length - 3} more` : authors.join(', ');
}

// The abstract text is fetched from the local server only when the row is opened.
function Abstract({ id, year, doi }) {
  const [open, setOpen] = useState(false);
  const text = useQuery({ queryKey: ['conference-abstract', id, year, doi], queryFn: () => conferencesApi.abstract(id, year, doi),
    enabled: open, retry: false, networkMode: 'always' });
  return <details className="mt-1" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="flex min-h-11 cursor-pointer items-center text-sm text-forest-800 underline underline-offset-4 sm:min-h-8">Abstract</summary>
    <Async loading={text.isPending && open} error={text.error}>
      <p className="whitespace-pre-line pb-2 text-sm text-slate-700">{text.data?.abstract || 'The source has no abstract text for this entry.'}</p>
    </Async>
  </details>;
}

export function PaperRow({ row, id, year }) {
  const late = row.level === 'late-breaking';
  return <li className="min-w-0 border-b border-slate-200 py-2">
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className="text-sm tabular-nums text-slate-600">{row.number}</span>
      {late && <span className="rounded-full border border-forest-800 px-2 text-xs text-forest-800">Late-breaking</span>}
    </div>
    <a href={row.url} target="_blank" rel="noreferrer" className="block break-words font-medium text-slate-900 underline-offset-4 hover:underline">{row.title}</a>
    {row.authors.length > 0 && <p className="break-words text-sm text-slate-600">{authorLine(row.authors)}</p>}
    <p className="text-xs text-slate-600">{late ? '' : 'Level unknown · '}{row.why}</p>
    {row.has_abstract ? <Abstract id={id} year={year} doi={row.doi} /> : <p className="text-xs text-slate-600">No abstract text from the source.</p>}
  </li>;
}

export function RowList({ rows, id, year }) {
  return <ul>{rows.map((row) => <PaperRow key={row.doi} row={row} id={id} year={year} />)}</ul>;
}
