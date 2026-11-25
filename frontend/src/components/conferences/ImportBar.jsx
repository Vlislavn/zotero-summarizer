import { useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { conferencesApi } from '../../api/conferencesApi.js';
import Async from '../ui/Async.jsx';
import Button from '../ui/Button.jsx';

// Adds the owner's own CSV/XLSX (e.g. abstracts of a title-only source) to this list. Read on this computer; no network.
export default function ImportBar({ id, year }) {
  const client = useQueryClient();
  const input = useRef(null);
  const run = useMutation({ mutationFn: (file) => conferencesApi.importFile(id, year, file),
    onSettled: () => { client.invalidateQueries({ queryKey: ['conferences'] }); client.invalidateQueries({ queryKey: ['conference', id, year] }); } });
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <Button variant="secondary" className="min-h-11" disabled={run.isPending} onClick={() => input.current?.click()}>{run.isPending ? 'Importing…' : 'Import abstracts…'}</Button>
      <p className="text-sm text-slate-700">CSV or XLSX with columns number, title, authors, abstract, doi, session. Read on this computer only.</p>
    </div>
    <input ref={input} type="file" accept=".csv,.xlsx" className="sr-only" tabIndex={-1} aria-label="Choose a CSV or XLSX file to import"
      onChange={(event) => { const [file] = event.target.files; event.target.value = ''; if (file) run.mutate(file); }} />
    <Async error={run.error} />
    {run.data && <p role={run.data.ok ? 'status' : 'alert'} className={`text-sm ${run.data.ok ? 'text-slate-700' : 'rounded-lg border border-amber-200 bg-amber-50 p-2 text-amber-800'}`}>{run.data.message}</p>}
  </div>;
}
