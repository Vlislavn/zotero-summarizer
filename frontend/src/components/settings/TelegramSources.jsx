import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { telegramFeedApi } from '../../api/researchFeedApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, CheckboxField, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

function selection(data) {
  return { sources: data.sources, model: data.model, limits: data.limits };
}
const LIMITS = [
  ['posts_per_channel', 'Newest posts per channel', 1, 3], ['replies_per_thread', 'Newest replies per thread / group messages', 1, 30],
  ['messages', 'Model source messages', 1, 120], ['prompt_characters', 'Prompt characters', 1000, 24000],
  ['context_tokens', 'Local context tokens', 2048, 32768], ['output_tokens', 'Local output tokens', 512, 4096],
];

export default function TelegramSources() {
  const cache = useQueryClient();
  const [draft, setDraft] = useState(null);
  const [consent, setConsent] = useState(false);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState(null);
  const sources = useQuery({ queryKey: ['telegram-sources'], queryFn: telegramFeedApi.sources, retry: false });
  const status = useQuery({ queryKey: ['telegram-status'], queryFn: telegramFeedApi.status, retry: false,
    refetchInterval: running ? 1500 : false });
  const save = useMutation({ mutationFn: telegramFeedApi.save, onSuccess: (value) => {
    cache.setQueryData(['telegram-sources'], value); setDraft(null); setConsent(false);
    setNotice('Telegram sources saved. Saving did not collect or call a model.');
  } });
  const run = useMutation({ mutationFn: telegramFeedApi.run, onMutate: () => setRunning(true),
    onSettled: () => { setRunning(false); setConsent(false); }, onSuccess: (value) => {
      cache.setQueryData(['telegram-status'], value); cache.invalidateQueries({ queryKey: ['weekly-runs'] });
    } });
  const value = draft || (sources.data ? selection(sources.data) : null);
  const dirty = draft && JSON.stringify(draft) !== JSON.stringify(selection(sources.data));
  function change(next) { setDraft(next); setConsent(false); setNotice(null); }
  function sourceChange(index, update) {
    change({ ...value, sources: value.sources.map((source, i) => i === index ? { ...source, ...update } : source) });
  }
  const observed = status.data;
  const pending = running || save.isPending;
  return <section aria-label="Telegram sources" className="space-y-4 border-t border-slate-200 pt-5">
    <h3 className="font-display text-xl text-forest-800">Telegram</h3>
    <p className="text-sm text-slate-600">Public channels with linked discussion replies, selected public groups, or a specific forum topic. No joins, sends, personal inbox or automatic work on save/view.</p>
    <p className="text-sm" role="status">{observed?.connection === 'verified'
      ? `Connected — verified ${observed.verified_at}. Historical verification, not a live guarantee.`
      : 'Not verified in this workflow. Prepare one-time authorization in the terminal: research-feed telegram-auth.'}
      {observed?.state && ` Last observed action: ${observed.state}${observed.updated_at ? ` (${observed.updated_at})` : ''}.`}
    </p>
    {status.isError && <Banner kind="error">Status unavailable; connection is unknown. {humanizeError(status.error)}</Banner>}
    {sources.isError && <Banner kind="error">Saved Telegram selections unavailable. {humanizeError(sources.error)}</Banner>}
    {notice && <Banner kind="success">{notice}</Banner>}
    {(save.error || run.error || observed?.error) && <Banner kind="error">{save.error ? humanizeError(save.error) : run.error ? humanizeError(run.error) : observed.error} No automatic retry. Check the saved report for partial coverage; a disconnected browser does not cancel server work.</Banner>}
    {value && <>
      {value.sources.map((source, index) => <div key={index} className="grid gap-3 md:grid-cols-[auto_1fr_auto] items-end">
        <label className="text-sm">Scope
          <select className="block border rounded-lg p-2" value={source.kind} disabled={pending}
            onChange={(event) => sourceChange(index, { kind: event.target.value, message_id: null })}>
            <option value="broadcast">Channel news + replies</option><option value="group">Discussion group</option><option value="topic">Forum topic</option>
          </select>
        </label>
        <Field label={`Telegram source ${index + 1}`} value={source.username} disabled={pending}
          placeholder={source.kind === 'topic' ? 'https://t.me/group/topic-id' : '@username or https://t.me/channel'}
          onChange={(username) => sourceChange(index, { username, message_id: null })} />
        <Button size="sm" variant="secondary" disabled={pending} onClick={() => change({ ...value, sources: value.sources.filter((_, i) => i !== index) })}>Remove source {index + 1}</Button>
        {source.message_id && <p className="text-xs">Selected {source.kind === 'topic' ? 'topic root' : 'post'}: {source.message_id}</p>}
      </div>)}
      <Button size="sm" variant="secondary" disabled={pending || value.sources.length >= 3}
        onClick={() => change({ ...value, sources: [...value.sources, { kind: 'broadcast', username: '', message_id: null }] })}>Add Telegram source</Button>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm">Telegram local provider
          <select aria-label="Telegram local provider" className="block border rounded-lg p-2 w-full" disabled={pending}
            value={value.model?.provider || ''} onChange={(event) => change({ ...value, model: { provider: event.target.value, model: value.model?.model || '' } })}>
            <option value="">Choose a saved local provider</option>
            {(sources.data.local_providers || []).map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
        <Field label="Telegram local model" value={value.model?.model || ''} disabled={pending}
          onChange={(model) => change({ ...value, model: { provider: value.model?.provider || '', model } })} />
      </div>
      <p className="text-xs text-slate-600">Explicit Telegram-only route; never falls back to global/feed models. Use an already prepared local model. Locality is not a hardware-safety certificate.</p>
      <details><summary className="cursor-pointer text-sm">Bounded work limits</summary>
        <p className="text-xs my-2">Newest snapshots, not complete history. Oversized prompts refuse rather than silently clipping text. One model attempt; no paper verification. Larger context may require more RAM.</p>
        <div className="grid gap-3 md:grid-cols-2">{LIMITS.map(([key, label, min, max]) => <Field key={key} label={label}
          type="number" min={min} max={max} value={value.limits[key]} disabled={pending}
          onChange={(raw) => change({ ...value, limits: { ...value.limits, [key]: Number(raw) } })} />)}</div>
      </details>
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" disabled={pending || sources.isError || !value.sources.every((source) => source.username.trim())}
          onClick={() => save.mutate(value)}>Save Telegram sources</Button>
        <Button variant="secondary" disabled={pending} onClick={() => { sources.refetch(); status.refetch(); }}>Refresh saved Telegram status</Button>
      </div>
      <CheckboxField label="Allow bounded read-only collection and local processing of these saved Telegram sources"
        checked={consent} onChange={setConsent} disabled={pending || Boolean(dirty)} />
      {dirty && <p className="text-sm">Save your changed Telegram selection before collecting.</p>}
      <Button disabled={pending || !consent || dirty || sources.isError || !value.sources.length || !value.model?.provider || !value.model?.model || !sources.data.llm_enabled}
        onClick={() => run.mutate({ consent: true })}>{running ? 'Collecting / reviewing…' : 'Collect & review selected sources'}</Button>
    </>}
    {observed?.report_id && <p><a className="underline text-forest-800" href={`/weekly?run=${encodeURIComponent(observed.report_id)}`}>Open saved Telegram report in Weekly</a></p>}
    {!observed?.report_id && observed?.collection_report_id && <p><a className="underline" href={`/weekly?run=${encodeURIComponent(observed.collection_report_id)}`}>Open collected snapshot and coverage in Weekly</a></p>}
  </section>;
}
