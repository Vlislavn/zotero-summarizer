import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchPending,
  applyPending,
  rejectPending,
  savePendingChangeEdit,
  fetchCollections,
} from '../api/pendingApi.js';
import {
  STATUS_TABS,
  flattenCollections,
  buildDraft,
  previewChange,
  changeBadgeClass,
  changeTypeLabel,
  collectionOptionLabel,
  buildPayloadFromDraft,
} from './pendingHelpers.js';

// Pending Changes page — port of the `activeTab === 'pending'` block from
// zotero_summarizer/web/ui.html. Provides functional parity:
//   - filter by status (pending / applied / rejected / failed)
//   - select / apply / reject batches of pending rows
//   - inline edit drafts for tag_changes, add/remove_from_collection, add_note
// Helpers extracted to ./pendingHelpers.js to keep this file under budget.

function StatusBanner({ message, isError }) {
  if (!message) return null;
  const cls = isError
    ? 'bg-rose-50 border-rose-200 text-rose-800'
    : 'bg-emerald-50 border-emerald-200 text-emerald-800';
  return <div className={`my-2 p-2 rounded-lg border text-xs ${cls}`}>{message}</div>;
}

function ChangeEditor({ change, drafts, setDrafts, flatCollections, saving, onSave }) {
  const getField = (field) => {
    const existing = drafts[change.id];
    if (existing && Object.prototype.hasOwnProperty.call(existing, field)) {
      return existing[field];
    }
    const next = buildDraft(change);
    // Lazily seed the draft so controlled inputs always have a value.
    setDrafts((prev) => (prev[change.id] ? prev : { ...prev, [change.id]: next }));
    return next[field] || '';
  };

  const setField = (field, value) => {
    setDrafts((prev) => {
      const merged = { ...buildDraft(change), ...(prev[change.id] || {}) };
      merged[field] = value;
      if (field === 'collection_key') {
        const entry = flatCollections.find((n) => String(n.key) === String(value || ''));
        if (entry) merged.collection_path = String(entry.name || '').trim();
      }
      return { ...prev, [change.id]: merged };
    });
  };

  const SaveBtn = () => (
    <button
      type="button"
      onClick={onSave}
      disabled={Boolean(saving[change.id])}
      className="px-3 py-1.5 rounded bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-300 disabled:text-slate-500"
    >
      {saving[change.id] ? 'Saving...' : 'Save edit'}
    </button>
  );

  if (change.change_type === 'tag_changes') {
    return (
      <div className="ml-6 mt-2 grid md:grid-cols-2 gap-2 text-xs">
        <div>
          <label className="text-slate-500">Add tags (comma-separated)</label>
          <input
            type="text"
            value={getField('add_tags_text')}
            onChange={(e) => setField('add_tags_text', e.target.value)}
            className="mt-1 w-full px-2 py-1.5 border border-slate-300 rounded"
          />
        </div>
        <div>
          <label className="text-slate-500">Remove tags (comma-separated)</label>
          <input
            type="text"
            value={getField('remove_tags_text')}
            onChange={(e) => setField('remove_tags_text', e.target.value)}
            className="mt-1 w-full px-2 py-1.5 border border-slate-300 rounded"
          />
        </div>
        <div className="md:col-span-2">
          <SaveBtn />
        </div>
      </div>
    );
  }

  if (
    change.change_type === 'add_to_collection'
    || change.change_type === 'remove_from_collection'
  ) {
    const currentPath = getField('collection_path');
    return (
      <div className="ml-6 mt-2 grid gap-2 text-xs">
        <div>
          <label className="text-slate-500">Target collection</label>
          <select
            value={getField('collection_key')}
            onChange={(e) => setField('collection_key', e.target.value)}
            className="mt-1 w-full px-2 py-1.5 border border-slate-300 rounded"
          >
            <option value="">Select collection</option>
            {flatCollections.map((entry) => (
              <option key={entry.key} value={entry.key}>
                {collectionOptionLabel(entry)}
              </option>
            ))}
          </select>
          {currentPath && (
            <div className="mt-1 text-[11px] text-slate-500">
              Current path: <span>{currentPath}</span>
            </div>
          )}
        </div>
        <div><SaveBtn /></div>
      </div>
    );
  }

  if (change.change_type === 'add_note') {
    return (
      <div className="ml-6 mt-2 grid gap-2 text-xs">
        <div>
          <label className="text-slate-500">Note title</label>
          <input
            type="text"
            value={getField('note_title')}
            onChange={(e) => setField('note_title', e.target.value)}
            className="mt-1 w-full px-2 py-1.5 border border-slate-300 rounded"
          />
        </div>
        <div>
          <label className="text-slate-500">Note HTML</label>
          <textarea
            rows={5}
            value={getField('note_html')}
            onChange={(e) => setField('note_html', e.target.value)}
            className="mt-1 w-full px-2 py-1.5 border border-slate-300 rounded font-mono"
          />
        </div>
        <div><SaveBtn /></div>
      </div>
    );
  }

  return null;
}

export default function Pending() {
  const [status, setStatus] = useState('pending');
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState({});
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);
  const [flatCollections, setFlatCollections] = useState([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchCollections();
        if (!cancelled) setFlatCollections(flattenCollections(data?.items || []));
      } catch {
        // Non-fatal — only affects the collection-edit dropdown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(async (currentStatus = status) => {
    setLoading(true);
    try {
      const data = await fetchPending({ status: currentStatus, limit: 500 });
      const items = data?.items || [];
      setPending(items);
      setSelected((prev) => {
        const ids = new Set(items.map((i) => i.id));
        const next = new Set();
        for (const id of prev) if (ids.has(id)) next.add(id);
        return next;
      });
    } catch (err) {
      setMessage(err.message || 'Failed to load changes.');
      setIsError(true);
      setPending([]);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(status); }, [load, status]);

  const setStatusAndReset = useCallback((next) => {
    setStatus(next);
    setSelected(new Set());
    setDrafts({});
    setMessage('');
    setIsError(false);
  }, []);

  const groupedPending = useMemo(() => {
    const map = new Map();
    for (const item of pending) {
      if (!map.has(item.item_key)) {
        map.set(item.item_key, {
          item_key: item.item_key,
          item_title: item.item_title,
          changes: [],
        });
      }
      map.get(item.item_key).changes.push(item);
    }
    return [...map.values()];
  }, [pending]);

  const selectedLabel = useMemo(() => {
    const row = STATUS_TABS.find((t) => t.value === status);
    return row ? row.label.toLowerCase() : status;
  }, [status]);

  function toggleOne(id) {
    if (status !== 'pending') return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function selectAll(all) {
    if (status !== 'pending') return;
    setSelected(all ? new Set(pending.map((i) => i.id)) : new Set());
  }

  async function handleSaveEdit(change) {
    if (status !== 'pending') {
      setMessage('Switch to Pending status before editing.');
      setIsError(true);
      return;
    }
    const payload = buildPayloadFromDraft(change, drafts[change.id], flatCollections);
    if (!payload) {
      setMessage('This change type cannot be edited here.');
      setIsError(true);
      return;
    }
    setSaving((prev) => ({ ...prev, [change.id]: true }));
    try {
      const data = await savePendingChangeEdit(change.id, payload);
      setMessage('Pending change updated.');
      setIsError(false);
      setDrafts((prev) => ({
        ...prev,
        [change.id]: buildDraft({ ...change, payload_json: data?.payload }),
      }));
      await load(status);
    } catch (err) {
      setMessage(err.message || 'Failed to save pending change.');
      setIsError(true);
    } finally {
      setSaving((prev) => ({ ...prev, [change.id]: false }));
    }
  }

  async function handleApply(force = false) {
    if (status !== 'pending') {
      setMessage('Only pending rows can be applied.');
      setIsError(true);
      return;
    }
    if (!selected.size) return;
    try {
      const data = await applyPending([...selected], { force });
      if (data?.error === 'zotero_running' && !force) {
        if (window.confirm('Zotero appears to be running. Apply anyway?')) {
          await handleApply(true);
        }
        return;
      }
      setSelected(new Set());
      const inboxRemoved = Number(data?.inbox_removed || 0);
      setMessage(
        inboxRemoved > 0
          ? `Selected changes applied. Removed from Inbox: ${inboxRemoved}.`
          : 'Selected changes applied.',
      );
      setIsError(false);
      await load(status);
    } catch (err) {
      setMessage(err.message || 'Failed to apply changes.');
      setIsError(true);
    }
  }

  async function handleReject() {
    if (status !== 'pending') {
      setMessage('Only pending rows can be rejected.');
      setIsError(true);
      return;
    }
    if (!selected.size) return;
    try {
      await rejectPending([...selected]);
      setSelected(new Set());
      setMessage('Selected changes rejected.');
      setIsError(false);
      await load(status);
    } catch (err) {
      setMessage(err.message || 'Failed to reject changes.');
      setIsError(true);
    }
  }

  const isPending = status === 'pending';

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div>
          <h2 className="text-lg font-bold">Pending Changes</h2>
          <p className="text-xs text-slate-500">Review, edit, and apply queued updates to Zotero.</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <button type="button" onClick={() => selectAll(true)}
            className="px-2 py-1 rounded bg-slate-100 hover:bg-slate-200">Select all</button>
          <button type="button" onClick={() => selectAll(false)}
            className="px-2 py-1 rounded bg-slate-100 hover:bg-slate-200">Clear</button>
          <button
            type="button"
            onClick={() => handleApply(false)}
            disabled={selected.size === 0 || !isPending}
            className="px-3 py-1.5 rounded bg-green-700 text-white hover:bg-green-800 disabled:bg-slate-300 disabled:text-slate-500"
          >
            Apply selected
          </button>
          <button
            type="button"
            onClick={handleReject}
            disabled={selected.size === 0 || !isPending}
            className="px-3 py-1.5 rounded bg-amber-600 text-white hover:bg-amber-700 disabled:bg-slate-300 disabled:text-slate-500"
          >
            Reject selected
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {STATUS_TABS.map((tab) => (
          <button
            type="button"
            key={tab.value}
            onClick={() => setStatusAndReset(tab.value)}
            className={`px-3 py-1.5 rounded-lg border text-xs font-semibold ${
              status === tab.value
                ? 'bg-teal-700 text-white border-teal-700'
                : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <StatusBanner message={message} isError={isError} />

      {!isPending && (
        <div className="mb-3 text-xs text-slate-500">
          Switch to Pending status to apply/reject or edit changes.
        </div>
      )}

      {groupedPending.map((group) => (
        <div
          key={group.item_key}
          className="mb-3 border border-slate-200 rounded-xl bg-white overflow-hidden"
        >
          <div className="px-3 py-2 bg-slate-50 border-b border-slate-200">
            <div className="font-semibold">{group.item_title || group.item_key}</div>
            <div className="text-xs text-slate-500 mono">{group.item_key}</div>
          </div>
          <div className="p-3 space-y-2">
            {group.changes.map((change) => (
              <div key={change.id} className="border border-slate-100 rounded-lg p-2">
                <label className="flex items-start gap-2 text-sm rounded-lg hover:bg-slate-50 transition-colors cursor-pointer">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    disabled={!isPending}
                    checked={selected.has(change.id)}
                    onChange={() => toggleOne(change.id)}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${changeBadgeClass(change.change_type)}`}>
                        {changeTypeLabel(change.change_type)}
                      </span>
                      <span className="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-600">
                        status: {change.status || 'pending'}
                      </span>
                    </div>
                    <div className="text-xs text-slate-600 mt-1 break-words">
                      {previewChange(change)}
                    </div>
                    {change.applied_at && (
                      <div className="text-[11px] text-slate-500 mt-1">
                        Applied at: {change.applied_at}
                      </div>
                    )}
                    {change.error_message && (
                      <div className="text-[11px] text-rose-700 mt-1">{change.error_message}</div>
                    )}
                  </div>
                </label>

                {isPending && (
                  <ChangeEditor
                    change={change}
                    drafts={drafts}
                    setDrafts={setDrafts}
                    flatCollections={flatCollections}
                    saving={saving}
                    onSave={() => handleSaveEdit(change)}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {loading && <div className="text-sm text-slate-500">Loading changes...</div>}
      {!loading && pending.length === 0 && (
        <div className="text-sm text-slate-500">No {selectedLabel} changes.</div>
      )}
    </div>
  );
}
