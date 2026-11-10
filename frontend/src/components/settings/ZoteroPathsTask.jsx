import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updatePaths } from '../../api/setupApi.js';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';
import { taskReturnTo, returnLabel } from '../../utils/settingsNavigation.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { invalidateSetupDoctor } from '../../utils/setupDoctorState.js';
import { Banner, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';
import TaskPage from './TaskPage.jsx';

function PathStatus({ info }) {
  const known = info?.set;
  const exists = info?.exists;
  return <p className={`text-xs ${exists ? 'text-emerald-700' : known ? 'text-rose-700' : 'text-slate-600'}`}>
    Running PDF root: {exists ? 'exists' : known ? 'missing' : 'unset'}. This is process status, not a check of your draft.
  </p>;
}

export default function ZoteroPathsTask() {
  const location = useLocation();
  const cache = useQueryClient();
  const { status, isError } = useSetupStatus();
  const destination = taskReturnTo(location);
  const [base, setBase] = useState(null);
  const [draft, setDraft] = useState({ zotero_data_dir: '', pdf_root: '' });
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (base === null && status?.paths) {
      const value = { zotero_data_dir: status.paths.zotero_data_dir?.value || '', pdf_root: status.paths.pdf_root?.value || '' };
      setBase(value); setDraft(current => Object.values(current).some(Boolean) ? current : value);
    }
  }, [base, status?.paths]);
  const updates = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value && value !== base?.[key]));
  const dirty = Object.keys(updates).length > 0;
  const mutation = useMutation({ mutationFn: submitted => updatePaths(submitted.body),
    onSettled: (_response, error) => invalidateSetupDoctor(cache, error),
    onSuccess: (_response, submitted) => {
      const next = { ...base, ...submitted.body };
      setBase(next);
      setDraft(current => Object.fromEntries(Object.keys(current).map(key => [key,
        current[key] === submitted.draft[key] ? next[key] || '' : current[key],
      ])));
      setNotice('Paths written. Restart required to apply them; the running process is unchanged.');
      cache.invalidateQueries({ queryKey: ['setup-status'] });
    },
  });
  function change(key, value) { setDraft(current => ({ ...current, [key]: value })); setNotice(''); }
  function save(event) {
    event.preventDefault();
    if (dirty && !mutation.isPending) { setNotice(''); mutation.mutate({ body: updates, draft: { ...draft } }); }
  }
  return <TaskPage title="Zotero & PDF paths" backTo={destination} backLabel={returnLabel(destination)} dirty={dirty} pending={mutation.isPending}
    description="Zotero is optional for RSS reading. Path changes are written separately and require a restart; this task does not restart the app.">
    {isError && <p role="alert">Current path status is unavailable. Filled paths are explicit replacements; blank fields keep the current values.</p>}
    <form onSubmit={save} className="space-y-5" id="essentials-zotero-paths">
      <div className="grid md:grid-cols-2 gap-4">
        <Field label="Zotero data directory" value={draft.zotero_data_dir} onChange={value => change('zotero_data_dir', value)}
          placeholder="/Users/you/Zotero" hint="Folder containing zotero.sqlite and storage/." />
        <Field label="PDF root (optional)" value={draft.pdf_root} onChange={value => change('pdf_root', value)}
          placeholder="Defaults to the Zotero storage/ folder" hint="Where generated paper-read artifacts are written next to the PDF." />
      </div>
      <PathStatus info={status?.paths?.pdf_root} />
      {mutation.error && <div role="alert"><Banner kind="error">{humanizeError(mutation.error)}</Banner></div>}
      {notice && <Banner kind="success">{notice}</Banner>}
      <p className="text-sm text-slate-600">Only changed, filled paths are written. Blank keeps the current value. Leaving does not undo paths already saved.</p>
      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={!dirty || mutation.isPending}>{mutation.isPending ? 'Saving…' : 'Save paths'}</Button>
        {dirty && <Button type="button" variant="secondary" disabled={mutation.isPending} onClick={() => {
          setDraft({ zotero_data_dir: base?.zotero_data_dir || '', pdf_root: base?.pdf_root || '' }); setNotice('Local draft discarded. Earlier path writes are not undone.');
        }}>Discard draft</Button>}
      </div>
    </form>
  </TaskPage>;
}
