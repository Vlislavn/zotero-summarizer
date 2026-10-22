import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addRssFeed,
  deleteRssFeed,
  fetchRssFeeds,
  importRssFeedsFromZotero,
  refreshRssFeeds,
  updateRssFeed,
} from '../../api/settingsApi.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, CheckboxField, Field, SectionCard } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const HACKERNOON_FEEDS = [
  { name: 'HackerNoon AI Agents', url: 'https://hackernoon.com/tagged/ai-agents/feed' },
  { name: 'HackerNoon Agentic AI', url: 'https://hackernoon.com/tagged/agentic-ai/feed' },
];

function subscriptionUrlKey(value) {
  try {
    const url = new URL(value);
    url.hash = '';
    return url.href;
  } catch {
    return value;
  }
}

export default function RssFeedsSection() {
  const queryClient = useQueryClient();
  const feedsQuery = useQuery({ queryKey: ['rss-feeds'], queryFn: fetchRssFeeds });
  const [draft, setDraft] = useState({ name: '', url: '', enabled: true });
  const [editing, setEditing] = useState({});
  const [notice, setNotice] = useState(null);

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ['rss-feeds'] });
  }

  function showError(error) {
    setNotice({ kind: 'error', text: humanizeError(error) });
  }

  function sourceWriteFailed(error) {
    showError(error);
    if (!error?.status || error.status >= 500) return invalidate();
  }

  const addMutation = useMutation({
    mutationFn: addRssFeed,
    onError: sourceWriteFailed,
    onSuccess: () => {
      setNotice({ kind: 'success', text: 'Feed saved. Refresh RSS now to fetch items.' });
      return invalidate();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }) => updateRssFeed(id, payload),
    onError: sourceWriteFailed,
    onSuccess: () => {
      setNotice({ kind: 'success', text: 'Feed updated' });
      return invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRssFeed,
    onError: sourceWriteFailed,
    onSuccess: () => {
      setNotice({ kind: 'success', text: 'Feed deleted' });
      return invalidate();
    },
  });

  const importMutation = useMutation({
    mutationFn: importRssFeedsFromZotero,
    onError: sourceWriteFailed,
    onSuccess: (res) => {
      setNotice({ kind: 'success', text: `Imported ${res.imported || 0} feed${res.imported === 1 ? '' : 's'}` });
      return invalidate();
    },
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshRssFeeds({ max_feeds: 10, max_new_items_per_feed: 25 }),
    onError: showError,
    onSuccess: (res) => {
      if (res.offline) {
        setNotice({ kind: 'error', text: 'RSS refresh skipped: the app is offline. Sources were not checked.' });
        return;
      }
      const counts = `Checked ${res.feeds || 0} feed${res.feeds === 1 ? '' : 's'}: ${res.inserted || 0} new, ${res.updated || 0} updated.`;
      const detail = res.errors?.length
        ? ` ${res.errors.length} feed(s) failed — see their errors below.`
        : !res.inserted && !res.updated ? ' No items stored in this pass.' : '';
      setNotice({ kind: res.errors?.length ? 'error' : 'success', text: counts + detail });
    },
    onSettled: invalidate,
  });

  const feeds = feedsQuery.data?.feeds || [];
  const sourceWritePending = addMutation.isPending || importMutation.isPending || updateMutation.isPending || deleteMutation.isPending;

  return (
    <SectionCard
      title="RSS feeds"
      description="App-owned subscriptions used by the Today pipeline."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => importMutation.mutate()}
            disabled={sourceWritePending}
          >
            {importMutation.isPending ? 'Importing...' : 'Import feeds from Zotero'}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
          >
            {refreshMutation.isPending ? 'Refreshing...' : 'Refresh RSS now'}
          </Button>
        </div>

        <Banner kind={notice?.kind}>{notice?.text}</Banner>
        {feedsQuery.isError && (
          <div className="space-y-2">
            <Banner kind="error">{humanizeError(feedsQuery.error)} Saved source state is unavailable; any displayed rows may be outdated.</Banner>
            <Button size="sm" variant="secondary" onClick={() => feedsQuery.refetch()} disabled={feedsQuery.isFetching}>
              Retry loading feeds
            </Button>
          </div>
        )}

        <div className="space-y-2">
          <p className="text-sm text-slate-700">HackerNoon engineering sources (optional)</p>
          <p className="text-xs text-slate-500">Add either tag feed explicitly. Upstream feeds can be empty or unavailable; saving is not a fetch or review.</p>
          <div className="flex flex-wrap gap-2">
            {HACKERNOON_FEEDS.map((preset) => {
              const saved = feeds.find((feed) => subscriptionUrlKey(feed.url) === preset.url);
              const adding = addMutation.isPending && addMutation.variables?.url === preset.url;
              return (
                <Button
                  key={preset.url}
                  variant="secondary"
                  size="sm"
                  disabled={Boolean(saved) || feedsQuery.isPending || feedsQuery.isFetching || feedsQuery.isError || sourceWritePending}
                  onClick={() => addMutation.mutate({ ...preset, enabled: true, create_only: true })}
                >
                  {saved ? `${preset.name} saved${saved.enabled ? '' : ' (disabled)'}` : adding ? `Adding ${preset.name}…` : `Add ${preset.name}`}
                </Button>
              );
            })}
          </div>
          <p className="text-xs text-slate-500">
            Refresh only fetches RSS (up to 10 enabled feeds per pass). Then use{' '}
            <a className="text-teal-700 underline" href="/today">Today → Triage backlog</a>{' '}
            for the shared ranking and review path. Empty feeds provide no candidates.
          </p>
        </div>

        <div className="grid gap-3 md:grid-cols-[1fr_2fr_auto] md:items-end">
          <Field
            label="Name"
            value={draft.name}
            onChange={(name) => setDraft((prev) => ({ ...prev, name }))}
          />
          <Field
            label="URL"
            value={draft.url}
            onChange={(url) => setDraft((prev) => ({ ...prev, url }))}
          />
          <Button
            onClick={() => addMutation.mutate(draft, {
              onSuccess: () => setDraft({ name: '', url: '', enabled: true }),
            })}
            disabled={sourceWritePending || !draft.name.trim() || !draft.url.trim()}
            className="md:mb-0.5"
          >
            Add feed
          </Button>
        </div>

        <div className="divide-y divide-slate-200 border border-slate-200 rounded-xl overflow-hidden">
          {feedsQuery.isPending ? (
            <div className="p-3 text-sm text-slate-500">Loading feeds...</div>
          ) : feedsQuery.isError && feeds.length === 0 ? (
            <div className="p-3 text-sm text-slate-500">Source list unavailable.</div>
          ) : feeds.length === 0 ? (
            <div className="p-3 text-sm text-slate-500">No RSS feeds saved.</div>
          ) : (
            feeds.map((feed) => {
              const edit = editing[feed.id] || {
                name: feed.name || '',
                url: feed.url || '',
                enabled: Boolean(feed.enabled),
              };
              return (
                <div key={feed.id} className="p-3 space-y-3 bg-white/70">
                  <div className="grid gap-3 lg:grid-cols-[1fr_2fr_auto] lg:items-end">
                    <Field
                      label="Name"
                      value={edit.name}
                      onChange={(name) => setEditing((prev) => ({
                        ...prev,
                        [feed.id]: { ...edit, name },
                      }))}
                    />
                    <Field
                      label="URL"
                      value={edit.url}
                      onChange={(url) => setEditing((prev) => ({
                        ...prev,
                        [feed.id]: { ...edit, url },
                      }))}
                    />
                    <div className="flex gap-2 lg:pb-1">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => updateMutation.mutate({ id: feed.id, payload: edit })}
                        disabled={sourceWritePending}
                      >
                        Save
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => deleteMutation.mutate(feed.id)}
                        disabled={sourceWritePending}
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                    <CheckboxField
                      label="Enabled"
                      checked={edit.enabled}
                      onChange={(enabled) => setEditing((prev) => ({
                        ...prev,
                        [feed.id]: { ...edit, enabled },
                      }))}
                    />
                    <span>Source: {feed.source || 'app'}</span>
                    {feed.last_error && (
                      <span className="text-rose-700 min-w-0 max-w-full [overflow-wrap:anywhere]">Last error: {feed.last_error}</span>
                    )}
                    <span>{feed.last_fetched_at ? `Last checked: ${feed.last_fetched_at}` : 'Not refreshed yet'}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </SectionCard>
  );
}
