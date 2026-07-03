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

export default function RssFeedsSection() {
  const queryClient = useQueryClient();
  const feedsQuery = useQuery({ queryKey: ['rss-feeds'], queryFn: fetchRssFeeds });
  const [draft, setDraft] = useState({ name: '', url: '', enabled: true });
  const [editing, setEditing] = useState({});
  const [message, setMessage] = useState('');

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ['rss-feeds'] });
  }

  const addMutation = useMutation({
    mutationFn: addRssFeed,
    onSuccess: () => {
      setDraft({ name: '', url: '', enabled: true });
      setMessage('Feed saved');
      invalidate();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }) => updateRssFeed(id, payload),
    onSuccess: () => {
      setMessage('Feed updated');
      invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRssFeed,
    onSuccess: () => {
      setMessage('Feed deleted');
      invalidate();
    },
  });

  const importMutation = useMutation({
    mutationFn: importRssFeedsFromZotero,
    onSuccess: (res) => {
      setMessage(`Imported ${res.imported || 0} feed${res.imported === 1 ? '' : 's'}`);
      invalidate();
    },
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshRssFeeds({ max_feeds: 10, max_new_items_per_feed: 25 }),
    onSuccess: (res) => {
      setMessage(`Refresh stored ${res.inserted || 0} new item${res.inserted === 1 ? '' : 's'}`);
      invalidate();
    },
  });

  const error =
    addMutation.error ||
    updateMutation.error ||
    deleteMutation.error ||
    importMutation.error ||
    refreshMutation.error ||
    feedsQuery.error;

  const feeds = feedsQuery.data?.feeds || [];

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
            disabled={importMutation.isPending}
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

        <Banner kind={error ? 'error' : 'success'}>
          {error ? humanizeError(error) : message}
        </Banner>

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
            onClick={() => addMutation.mutate(draft)}
            disabled={addMutation.isPending || !draft.name.trim() || !draft.url.trim()}
            className="md:mb-0.5"
          >
            Add feed
          </Button>
        </div>

        <div className="divide-y divide-slate-200 border border-slate-200 rounded-xl overflow-hidden">
          {feedsQuery.isLoading ? (
            <div className="p-3 text-sm text-slate-500">Loading feeds...</div>
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
                        disabled={updateMutation.isPending}
                      >
                        Save
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => deleteMutation.mutate(feed.id)}
                        disabled={deleteMutation.isPending}
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
                    {feed.last_error ? (
                      <span className="text-rose-700">Last error: {feed.last_error}</span>
                    ) : feed.last_fetched_at ? (
                      <span>Last fetched: {feed.last_fetched_at}</span>
                    ) : null}
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
