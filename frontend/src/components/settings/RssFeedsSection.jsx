import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import { addRssFeed, deleteRssFeed, fetchRssFeeds, importRssFeedsFromZotero, refreshRssFeeds, updateRssFeed } from '../../api/settingsApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { invalidateSetupDoctor } from '../../utils/setupDoctorState.js';
import { taskUrl } from '../../utils/settingsNavigation.js';
import { Banner, CheckboxField, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';
import RssFeedRow from './RssFeedRow.jsx';

const HACKERNOON_FEEDS = [
  { name: 'HackerNoon AI Agents', url: 'https://hackernoon.com/tagged/ai-agents/feed' },
  { name: 'HackerNoon Agentic AI', url: 'https://hackernoon.com/tagged/agentic-ai/feed' },
];
const values = feed => ({ name: feed.name || '', url: feed.url || '', enabled: Boolean(feed.enabled) });
const EMPTY = { name: '', url: '', enabled: true };
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
function importNotice({ imported = 0, added = imported }) {
  if (!added) return imported ? `No new feeds: all ${plural(imported, 'feed')} from Zotero are already here.` : 'No feeds found in Zotero.';
  const present = imported - added;
  return `Added ${plural(added, 'new feed')}${present > 0 ? ` (${present} already present)` : ''}. Nothing fetched yet.`;
}
function refreshNotice({ feeds = 0, inserted = 0, updated = 0, unchanged, incomplete = 0, errors = [] }) {
  const failed = errors.length;
  if (!inserted && !updated && !failed) return `Nothing new: checked ${plural(feeds, 'feed')}, no new or changed items.${incomplete ? ` ${plural(incomplete, 'feed')} cut off by the source; the rest is read next time.` : ''}`;
  const parts = [`${inserted} new`, `${updated} updated`];
  if (unchanged) parts.push(`${unchanged} unchanged`);
  if (failed) parts.push(`${failed} failed`);
  const tail = failed ? ' Failed feeds are marked below.' : incomplete ? ` ${plural(incomplete, 'feed')} cut off by the source; the rest is read next time.` : '';
  return `Checked ${plural(feeds, 'feed')}: ${parts.join(', ')}.${tail}`;
}
function subscriptionUrlKey(value) {
  try { const url = new URL(value); url.hash = ''; return url.href; } catch { return value; }
}

export default function RssFeedsSection({ onDirtyChange, onPendingChange }) {
  const location = useLocation();
  const cache = useQueryClient();
  const feedsQuery = useQuery({ queryKey: ['rss-feeds'], queryFn: fetchRssFeeds });
  const [editor, setEditor] = useState(null);
  const [notice, setNotice] = useState(null);
  const editorHeading = useRef(null);
  const focusAfterClose = useRef(null);
  const dirty = Boolean(editor && (JSON.stringify(editor.draft) !== JSON.stringify(editor.original)));
  function invalidate() { return cache.invalidateQueries({ queryKey: ['rss-feeds'] }); }
  function showError(error) { setNotice({ kind: 'error', text: humanizeError(error) }); }
  function sourceWriteFailed(error) {
    showError(error);
    if (!error?.status || error.status >= 500) return invalidate();
  }
  const addMutation = useMutation({
    mutationFn: addRssFeed, onError: sourceWriteFailed,
    onSettled: (_response, error) => invalidateSetupDoctor(cache, error),
    onSuccess: () => { setNotice({ kind: 'success', text: 'Feed saved. Refresh RSS now to fetch items.' }); return invalidate(); },
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, payload }) => updateRssFeed(id, payload), onError: sourceWriteFailed,
    onSettled: (_response, error) => invalidateSetupDoctor(cache, error),
    onSuccess: () => { setNotice({ kind: 'success', text: 'Feed updated. Nothing fetched.' }); return invalidate(); },
  });
  const deleteMutation = useMutation({
    mutationFn: deleteRssFeed, onError: sourceWriteFailed,
    onSettled: (_response, error) => invalidateSetupDoctor(cache, error),
    onSuccess: (_response, id) => {
      setNotice({ kind: 'success', text: 'Feed deleted.' });
      if (editor && editor.id !== id) focusAfterClose.current = 'rss-editor-heading'; else closeEditor(null);
      return invalidate();
    },
  });
  const importMutation = useMutation({
    mutationFn: importRssFeedsFromZotero, onError: sourceWriteFailed,
    onSettled: (_response, error) => invalidateSetupDoctor(cache, error),
    onSuccess: result => { setNotice({ kind: 'success', text: importNotice(result) }); return invalidate(); },
  });
  const refreshMutation = useMutation({
    mutationFn: () => refreshRssFeeds({ max_feeds: 10 }), onError: showError,
    onSuccess: result => {
      if (result.offline) { setNotice({ kind: 'error', text: 'RSS refresh skipped: the app is offline. Sources were not checked.' }); return; }
      setNotice({ kind: result.errors?.length ? 'error' : 'success', text: refreshNotice(result) });
    }, onSettled: invalidate,
  });
  const sourceWritePending = addMutation.isPending || importMutation.isPending || updateMutation.isPending || deleteMutation.isPending;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onPendingChange?.(sourceWritePending || refreshMutation.isPending); }, [sourceWritePending, refreshMutation.isPending, onPendingChange]);
  const editorId = editor?.id;
  const editing = Boolean(editor);
  useEffect(() => { if (editing) editorHeading.current?.focus(); }, [editing, editorId]);
  useEffect(() => {
    if (!sourceWritePending && focusAfterClose.current) {
      const target = document.getElementById(focusAfterClose.current);
      if (target) { target.focus(); focusAfterClose.current = null; }
    }
  }, [editing, sourceWritePending, feedsQuery.data]);
  function closeEditor(id) { focusAfterClose.current = id == null ? 'rss-add' : `rss-edit-${id}`; setEditor(null); }
  function openEditor(feed = null) {
    if (dirty && !window.confirm('Discard the current RSS draft and edit another subscription?')) return;
    const original = feed ? values(feed) : { ...EMPTY };
    setEditor({ id: feed?.id ?? null, original, draft: { ...original } }); setNotice(null);
  }
  function change(field, value) {
    if (!sourceWritePending) setEditor(current => ({ ...current, draft: { ...current.draft, [field]: value } }));
  }
  function submit(event) {
    event.preventDefault();
    if (!editor || sourceWritePending || !editor.draft.name.trim() || !editor.draft.url.trim()) return;
    const success = { onSuccess: saved => closeEditor(editor.id ?? saved?.id ?? null) };
    if (editor.id === null) addMutation.mutate(editor.draft, success);
    else updateMutation.mutate({ id: editor.id, payload: editor.draft }, success);
  }
  const feeds = feedsQuery.data?.feeds || [];
  const failing = feeds.filter(feed => feed.last_error && !feed.last_error.startsWith('RssNamespaceWarning:')).length;
  return <div id="rss-feeds" className="space-y-5 min-w-0">
    <Banner kind={notice?.kind}>{notice?.text}</Banner>
    {feedsQuery.isError && <div className="space-y-3"><Banner kind="error">{humanizeError(feedsQuery.error)} Saved source state is unavailable; displayed rows may be outdated.</Banner>
      <Button variant="secondary" onClick={() => feedsQuery.refetch()} disabled={feedsQuery.isFetching}>Retry loading feeds</Button></div>}
    <div className="flex flex-wrap gap-3">
      <Button id="rss-add" onClick={() => openEditor()} disabled={sourceWritePending}>Add subscription</Button>
      <Button variant="secondary" onClick={() => importMutation.mutate()} disabled={sourceWritePending}>Import feeds from Zotero</Button>
    </div>
    {editor && <form onSubmit={submit} className="rounded-2xl border border-sage-500 bg-linen p-4 space-y-4">
      <h2 id="rss-editor-heading" ref={editorHeading} tabIndex={-1} className="font-display text-xl font-light focus:outline-none">{editor.id === null ? 'Add RSS subscription' : 'Edit RSS subscription'}</h2>
      <Field label="Name" value={editor.draft.name} disabled={sourceWritePending} onChange={value => change('name', value)} />
      <Field label="URL" value={editor.draft.url} disabled={sourceWritePending} onChange={value => change('url', value)} />
      <CheckboxField label="Enabled" checked={editor.draft.enabled} disabled={sourceWritePending} onChange={value => change('enabled', value)} />
      <p className="text-sm text-slate-600">Save changes to this subscription only. It does not fetch items or start triage.</p>
      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={sourceWritePending || !editor.draft.name.trim() || !editor.draft.url.trim() || (editor.id !== null && !dirty)}>{addMutation.isPending && !addMutation.variables?.create_only || updateMutation.isPending ? 'Saving…' : 'Save subscription'}</Button>
        <Button variant="secondary" disabled={sourceWritePending} onClick={() => closeEditor(editor.id)}>Discard subscription draft</Button>
      </div>
    </form>}
    {feeds.length > 0 && <p className="text-sm text-slate-600">{feeds.length} subscriptions · {feeds.filter(feed => feed.enabled).length} enabled{failing ? ` · ${failing} failing` : ''}</p>}
    <section aria-label="Saved RSS subscriptions" className="rounded-2xl border border-slate-200 divide-y divide-slate-200">
      {feedsQuery.isPending ? <p role="status" className="p-4">Loading feeds…</p>
        : feedsQuery.isError && !feeds.length ? <p className="p-4">Source list unavailable.</p>
        : !feeds.length ? <div className="p-4 text-sm text-slate-600"><p>No RSS feeds saved.</p><p>Add a subscription or import your feeds from Zotero.</p></div>
        : feeds.map(feed => <RssFeedRow key={feed.id} feed={feed} disabled={sourceWritePending} onEdit={openEditor}
          onDelete={target => { if (window.confirm(`Delete the RSS subscription “${target.name}”?`)) deleteMutation.mutate(target.id); }} />)}
    </section>
    <details className="rounded-xl border border-slate-200 p-4 space-y-3">
      <summary className="cursor-pointer text-sm text-forest-800">Suggested engineering feeds (optional)</summary>
      <p className="mt-3 text-sm text-slate-600">Add a HackerNoon tag feed explicitly. Upstream feeds can be empty or unavailable; saving is not a fetch or review.</p>
      <div className="flex flex-wrap gap-3">{HACKERNOON_FEEDS.map(preset => {
        const saved = feeds.find(feed => subscriptionUrlKey(feed.url) === preset.url);
        const adding = addMutation.isPending && addMutation.variables?.url === preset.url;
        return <Button key={preset.url} variant="secondary" disabled={Boolean(saved) || feedsQuery.isPending || feedsQuery.isFetching || feedsQuery.isError || sourceWritePending}
          onClick={() => addMutation.mutate({ ...preset, enabled: true, create_only: true })}>
          {saved ? `${preset.name} saved${saved.enabled ? '' : ' (disabled)'}` : adding ? `Adding ${preset.name}…` : `Add ${preset.name}`}
        </Button>;
      })}</div>
    </details>
    <section aria-label="Fetch and triage RSS" className="border-t border-slate-200 pt-5 space-y-3">
      <h2 className="font-display text-xl font-light text-forest-800">Fetch, then triage</h2>
      <p className="text-sm text-slate-600">Refresh only fetches RSS, up to 10 enabled feeds per pass. Then use Today → Triage backlog for ranking. Empty feeds provide no candidates.</p>
      <div className="flex flex-wrap gap-3 items-center"><Button variant="secondary" onClick={() => refreshMutation.mutate()} disabled={refreshMutation.isPending}>
        {refreshMutation.isPending ? 'Refreshing…' : 'Refresh RSS now'}</Button><Link to={taskUrl(location, null, '/today')} className="min-h-11 inline-flex items-center text-sm underline text-forest-800">Today → Triage backlog</Link></div>
    </section>
  </div>;
}
