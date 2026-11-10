import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { telegramFeedApi } from '../../api/researchFeedApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Link } from 'react-router-dom';
import { Banner, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';
import SettingsTaskLink from './SettingsTaskLink.jsx';
import { AuthCommand } from '../TelegramRunButton.jsx';
import { when } from '../telegramRunText.js';

const SELECT = 'mt-1 block min-h-11 rounded-lg border border-slate-300 bg-white p-2 text-sm sm:min-h-10';
function label({ username, kind }) {
  const name = /^https?:/.test(username) || !username ? username : `@${username.replace(/^@/, '')}`;
  return kind === 'broadcast' ? name : `${name} · ${kind}`;
}
function selection(data) {
  return { sources: data.sources, model: data.model, limits: data.limits };
}
const LIMITS = [
  ['posts_per_channel', 'Newest posts per channel', 1, 3], ['replies_per_thread', 'Newest replies per thread / group messages', 1, 300],
  ['messages', 'Model source messages', 1, 300], ['prompt_characters', 'Prompt characters', 1000, 24000],
  ['context_tokens', 'Local context tokens', 2048, 32768], ['output_tokens', 'Local output tokens', 512, 4096],
];

function Processing({ processing, dirty, pending, onAllow }) {
  if (!processing) return <p className="text-sm text-slate-600">Pick a provider and model for summaries and trend labels; without one only the top comments are shown.</p>;
  const where = `${processing.provider} / ${processing.model}`;
  if (processing.local) return <p className="text-sm text-slate-700" role="status">Summaries and trend labels: {where} — local, on this computer. Channel text is never sent anywhere.</p>;
  return <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm" role="status">
    <p>Summaries and trend labels: <span className="font-medium">{where}</span> — <span className="font-medium">external provider</span>. {processing.remote_consented
      ? 'You allowed sending it the text of channel posts and comments.'
      : 'Until you allow sending post and comment text there are no summaries — only the top comments.'}</p>
    <Button size="sm" variant="secondary" disabled={pending || dirty}
      onClick={() => onAllow(processing.token, !processing.remote_consented)}>
      {processing.remote_consented ? 'Revoke permission' : `Allow sending text to ${processing.provider}`}</Button>
    {dirty && <p className="text-xs">Save the provider choice first.</p>}
  </div>;
}

export default function TelegramSources({ onDirtyChange, onPendingChange }) {
  const cache = useQueryClient();
  const [draft, setDraft] = useState(null);
  const [notice, setNotice] = useState(null);
  const [adding, setAdding] = useState('');
  const sources = useQuery({ queryKey: ['telegram-sources'], queryFn: telegramFeedApi.sources, retry: false });
  const status = useQuery({ queryKey: ['telegram-status'], queryFn: telegramFeedApi.status, retry: false,
    refetchInterval: (query) => (query.state.data?.running ? 1500 : false) });
  const save = useMutation({ mutationFn: telegramFeedApi.save, onSuccess: (value) => {
    cache.setQueryData(['telegram-sources'], value); setDraft(null);
    setNotice('Telegram sources saved. Saving did not collect or call a model.');
  } });
  const allow = useMutation({ mutationFn: ({ token, on }) => telegramFeedApi.processing(token, on),
    onSuccess: (processing) => cache.setQueryData(['telegram-sources'], (old) => ({ ...old, processing })) });
  const consent = useMutation({ mutationFn: telegramFeedApi.collectConsent,
    onSuccess: () => cache.invalidateQueries({ queryKey: ['telegram-status'] }) });
  const value = draft || (sources.data ? selection(sources.data) : null);
  const dirty = draft && JSON.stringify(draft) !== JSON.stringify(selection(sources.data));
  function change(next) { setDraft(next); setNotice(null); }
  function sourceChange(index, update) {
    change({ ...value, sources: value.sources.map((source, i) => i === index ? { ...source, ...update } : source) });
  }
  const observed = status.data;
  const pending = Boolean(status.data?.running) || save.isPending || allow.isPending || consent.isPending;
  useEffect(() => { onDirtyChange?.(Boolean(dirty)); }, [dirty, onDirtyChange]);
  useEffect(() => { onPendingChange?.(pending); }, [pending, onPendingChange]);
  const connected = observed?.connection === 'verified';
  return <div aria-label="Telegram sources" role="region" className="space-y-6">
    <section aria-label="Telegram connection" className="space-y-2">
      <p className="flex items-start gap-2 text-sm text-slate-700" role="status" title="Historical verification, not a live guarantee">
        <span aria-hidden="true" className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${connected ? 'bg-forest-800' : 'border border-slate-400'}`} />
        <span>{connected
          ? `Connected · verified ${when(observed.verified_at)}.`
          : `Not verified in this workflow.${observed?.connection === 'needs_auth' ? ' Prepare one-time authorization in the terminal:' : ''}`}
          {observed?.state && ` Last action: ${observed.state}${observed.updated_at ? ` (${when(observed.updated_at)})` : ''}.`}</span>
      </p>
      {observed?.connection === 'needs_auth' && <AuthCommand />}
      {status.isError && <Banner kind="error">Status unavailable; connection is unknown. {humanizeError(status.error)}</Banner>}
      {sources.isError && <Banner kind="error">Saved Telegram selections unavailable. {humanizeError(sources.error)}</Banner>}
      {notice && <Banner kind="success">{notice}</Banner>}
      {(save.error || consent.error) && <Banner kind="error">{humanizeError(save.error || consent.error)}</Banner>}
    </section>
    {value && <>
      <section aria-label="Telegram channels" className="space-y-3 border-t border-slate-200 pt-5">
        <h2 className="font-display text-xl font-light text-forest-800">Channels</h2>
        <p className="text-sm text-slate-600">Public channels with linked discussions, public groups, or one forum topic. Read-only: no joining or sending. Posts are ranked by your Article-selection goals.</p>
        {value.sources.length === 0 && <p className="rounded-xl border border-slate-200 p-4 text-sm text-slate-600">No channels yet — add the first one below.</p>}
        {value.sources.length > 0 && <ul className="grid items-start gap-x-6 border-t border-slate-200 sm:grid-cols-2">
          {value.sources.map((source, index) => <li key={index} className="flex items-start gap-1 border-b border-slate-200">
            <details className="min-w-0 flex-1">
              <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-2 text-sm">
                <span className="truncate font-medium text-slate-900">{label(source)}</span><span className="text-xs text-slate-600">Edit</span>
              </summary>
              <div className="grid items-end gap-3 pb-3 md:grid-cols-[auto_1fr]">
                <label className="text-sm font-medium text-slate-700">Scope
                  <select className={SELECT} value={source.kind} disabled={pending}
                    onChange={(event) => sourceChange(index, { kind: event.target.value, message_id: null })}>
                    <option value="broadcast">Channel news + replies</option><option value="group">Discussion group</option><option value="topic">Forum topic</option>
                  </select>
                </label>
                <Field label={`Telegram source ${index + 1}`} value={source.username} disabled={pending}
                  placeholder={source.kind === 'topic' ? 'https://t.me/group/topic-id' : '@username or https://t.me/channel'}
                  onChange={(username) => sourceChange(index, { username, message_id: null })} />
                {source.message_id && <p className="text-xs text-slate-600">Selected {source.kind === 'topic' ? 'topic root' : 'post'}: {source.message_id}</p>}
              </div>
            </details>
            <button type="button" aria-label={`Remove ${label(source)}`} title="Remove" disabled={pending}
              className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 disabled:opacity-50"
              onClick={() => change({ ...value, sources: value.sources.filter((_, i) => i !== index) })}>✕</button>
          </li>)}
        </ul>}
        <form className="flex gap-2" onSubmit={(event) => {
          event.preventDefault();
          const name = adding.trim();
          if (!name || value.sources.length >= 30) return;
          change({ ...value, sources: [...value.sources, { kind: 'broadcast', username: name, message_id: null }] }); setAdding('');
        }}>
          <input aria-label="Add Telegram source" className="min-h-11 min-w-0 flex-1 rounded-lg border border-slate-300 bg-white p-2 text-sm" value={adding}
            disabled={pending || value.sources.length >= 30} placeholder="@name or t.me link"
            onChange={(event) => setAdding(event.target.value)} />
          <Button type="submit" variant="secondary" disabled={pending || !adding.trim() || value.sources.length >= 30}>Add Telegram source</Button>
        </form>
      </section>
      <section aria-label="Telegram summaries" className="space-y-3 border-t border-slate-200 pt-5">
        <h2 className="font-display text-xl font-light text-forest-800">Summaries</h2>
        <p className="text-sm text-slate-600">Telegram uses only the model chosen here, never the global or feed models. An external provider needs your separate permission.</p>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">Telegram provider
            <select aria-label="Telegram provider" className={`${SELECT} w-full`} disabled={pending}
              value={value.model?.provider || ''} onChange={(event) => change({ ...value, model: { provider: event.target.value, model: value.model?.model || '' } })}>
              <option value="">Choose a saved provider</option>
              {(sources.data.local_providers || []).map((name) => <option key={name} value={name}>{name}</option>)}
              {(sources.data.providers || []).map((name) => <option key={name} value={name}>{name} (external)</option>)}
            </select>
          </label>
          <Field label="Telegram model" value={value.model?.model || ''} disabled={pending}
            onChange={(model) => change({ ...value, model: { provider: value.model?.provider || '', model } })} />
        </div>
        <Processing processing={sources.data.processing} dirty={Boolean(dirty)} pending={pending}
          onAllow={(token, on) => allow.mutate({ token, on })} />
        {allow.error && <Banner kind="error">{humanizeError(allow.error)}</Banner>}
        <p className="flex flex-wrap items-center gap-x-3 text-sm text-slate-600">
          {!sources.data.local_providers?.length && <span>No saved local provider.</span>}
          <SettingsTaskLink task="connection" className="inline-flex min-h-11 items-center text-forest-800 underline underline-offset-4">Manage local AI connections</SettingsTaskLink>
        </p>
        <details className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-sm text-forest-800">Bounded work limits</summary>
          <p className="my-2 text-sm text-slate-600">Newest snapshots, not the whole history. Oversized prompts are refused, never silently clipped. Larger context needs more RAM.</p>
          <div className="grid gap-3 md:grid-cols-2">{LIMITS.map(([key, label, min, max]) => <Field key={key} label={label}
            kind="number" step={1} min={min} max={max} value={value.limits[key]} disabled={pending}
            onChange={(raw) => change({ ...value, limits: { ...value.limits, [key]: Number(raw) } })} />)}</div>
        </details>
      </section>
      <div className="flex flex-wrap gap-3">
        <Button variant={dirty ? 'primary' : 'secondary'} disabled={pending || sources.isError || !value.sources.every((source) => source.username.trim())}
          onClick={() => save.mutate(value)}>Save Telegram sources</Button>
        <Button variant="secondary" disabled={pending} onClick={() => { sources.refetch(); status.refetch(); }}>Refresh saved Telegram status</Button>
      </div>
      <section aria-label="Telegram access" className="space-y-3 border-t border-slate-200 pt-5">
        <h2 className="font-display text-xl font-light text-forest-800">Access</h2>
        <label className={`flex min-h-11 items-start gap-3 rounded-xl border border-slate-200 bg-white p-3 ${pending || status.isPending || status.isError ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
          <input type="checkbox" className="mt-0.5 h-5 w-5 shrink-0 accent-forest-800" checked={Boolean(observed?.consented)}
            disabled={pending || status.isPending || status.isError} onChange={(event) => consent.mutate(event.target.checked)} />
          <span className="text-sm">
            <span className="block font-medium text-slate-900">Allow reading these channels when I press “Check channels”</span>
            <span className="mt-0.5 block text-slate-600">{observed?.consented ? 'Allowed.' : 'Not allowed yet.'} Read-only: no joining or sending. Summaries come from the model chosen above. You can revoke this here at any time.</span>
          </span>
        </label>
        <p className="text-sm text-slate-600">{dirty ? 'Save your channel changes, then press “Check channels” on ' : 'Press “Check channels” on '}<Link className="text-forest-800 underline underline-offset-4" to="/weekly">Weekly</Link>.</p>
      </section>
    </>}
  </div>;
}
