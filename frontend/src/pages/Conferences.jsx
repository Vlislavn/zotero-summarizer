import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { conferencesApi } from '../api/conferencesApi.js';
import Async from '../components/ui/Async.jsx';
import PageTitle from '../components/ui/PageTitle.jsx';
import EditionPanel from '../components/conferences/EditionPanel.jsx';

const EXAMPLE = `conferences:
  - id: asco
    recipe: asco
    editions:
      2026: {from: 2026-05-20, until: 2026-06-30}`;

function ConfigNotice({ data }) {
  return <>
    {!data.config_exists && <div className="space-y-2 text-sm text-slate-700">
      <p>No conferences defined yet. Create <code>data/conferences/config.yaml</code> with the conferences you follow, for example:</p>
      <pre className="overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs">{EXAMPLE}</pre>
    </div>}
    {data.errors.length > 0 && <ul role="alert" className="space-y-1 rounded-lg border border-rose-200 bg-rose-50 p-2 text-sm text-rose-800">
      {data.errors.map((e) => <li key={`${e.line}-${e.message}`}>config.yaml line {e.line}: {e.message}</li>)}
    </ul>}
  </>;
}

export default function Conferences() {
  const [params, setParams] = useSearchParams();
  const list = useQuery({ queryKey: ['conferences'], queryFn: conferencesApi.list, retry: false, networkMode: 'always' });
  const data = list.data;
  const options = (data?.conferences || []).flatMap((c) => c.editions.map((e) => ({ key: `${c.id}/${e.year}`, label: `${c.label} ${e.year}`, conference: c, edition: e })));
  const chosen = options.find((o) => o.key === params.get('e')) || options[0];
  return <div className="mx-auto w-full min-w-0 max-w-3xl space-y-4">
    <PageTitle title="Conferences" page="conferences" />
    <Async loading={list.isPending} error={list.error}>
      {data && <ConfigNotice data={data} />}
      {chosen && <>
        <label className="block text-sm text-slate-700">Conference
          <select className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 bg-white p-2 text-sm" value={chosen.key}
            onChange={(event) => setParams({ e: event.target.value })}>
            {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </label>
        <EditionPanel key={chosen.key} conference={chosen.conference} edition={chosen.edition} />
      </>}
    </Async>
  </div>;
}
