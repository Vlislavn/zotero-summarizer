import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { conferencesApi } from '../../api/conferencesApi.js';
import Async from '../ui/Async.jsx';

function authorLine(authors) {
  if (!authors.length) return '';
  return authors.length > 3 ? `${authors.slice(0, 3).join(', ')} +${authors.length - 3} more` : authors.join(', ');
}

// The abstract text is fetched from the local server only when the row is opened.
function Abstract({ id, year, rowId }) {
  const [open, setOpen] = useState(false);
  const text = useQuery({ queryKey: ['conference-abstract', id, year, rowId], queryFn: () => conferencesApi.abstract(id, year, rowId),
    enabled: open, retry: false, networkMode: 'always' });
  return <details className="mt-1" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="flex min-h-11 cursor-pointer items-center text-sm text-forest-800 underline underline-offset-4 sm:min-h-8">Abstract</summary>
    <Async loading={text.isPending && open} error={text.error}>
      <p className="whitespace-pre-line pb-2 text-sm text-slate-700">{text.data?.abstract || 'The source has no abstract text for this entry.'}</p>
    </Async>
  </details>;
}

export function PaperRow({ row, id, year, titleOnly }) {
  const late = row.level === 'late-breaking';
  return <li className="min-w-0 border-b border-slate-200 py-2">
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className="text-sm tabular-nums text-slate-600">{row.number}</span>
      {late && <span className="rounded-full border border-forest-800 px-2 text-xs text-forest-800">Late-breaking</span>}
    </div>
    {row.url
      ? <a href={row.url} target="_blank" rel="noreferrer" className="block break-words font-medium text-slate-900 underline-offset-4 hover:underline">{row.title}</a>
      : <p className="break-words font-medium text-slate-900">{row.title}</p>}
    {row.authors.length > 0 && <p className="break-words text-sm text-slate-600">{authorLine(row.authors)}</p>}
    {row.session && <p className="break-words text-xs text-slate-600">{row.session}</p>}
    <p className="text-xs text-slate-600">{late ? '' : 'Level unknown · '}{row.why}</p>
    {row.has_abstract && <Abstract id={id} year={year} rowId={row.id} />}
    {!row.has_abstract && !titleOnly && <p className="text-xs text-slate-600">No abstract text from the source.</p>}
  </li>;
}

export function RowList({ rows, id, year, titleOnly }) {
  return <ul>{rows.map((row) => <PaperRow key={row.id} row={row} id={id} year={year} titleOnly={titleOnly} />)}</ul>;
}
