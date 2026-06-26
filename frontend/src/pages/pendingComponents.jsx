import {
  buildDraft,
  previewChange,
  changeBadgeTone,
  changeTypeLabel,
  collectionOptionLabel,
} from './pendingHelpers.js';
import { ActionBadge } from '../components/ui/Badge.jsx';

// Presentational subcomponents for the Pending Changes page, split out of
// Pending.jsx to keep the page file under its LOC budget. No data fetching or
// app state lives here — everything arrives via props from the page.

// StatusBanner now lives in components/library/shared.jsx (single source);
// re-exported here so Pending.jsx's existing import keeps resolving.
export { StatusBanner } from '../components/library/shared.jsx';

// Per-change inline draft editor. Three shapes keyed off change_type:
// tag_changes (add/remove tag inputs), add/remove_from_collection (a collection
// <select>), and add_note (title + HTML body). Drafts are lazily seeded from
// the change payload so controlled inputs always have a value.
export function ChangeEditor({ change, drafts, setDrafts, flatCollections, saving, onSave }) {
  const getField = (field) => {
    const existing = drafts[change.id];
    if (existing && Object.prototype.hasOwnProperty.call(existing, field)) {
      return existing[field];
    }
    // Pure read-through: derive the displayed value from the change's own
    // payload WITHOUT seeding state during render (that triggered React's
    // "Cannot update a component while rendering a different component"
    // warning). The draft is materialized on first edit via setField, and the
    // save path (buildPayloadFromDraft) already falls back to buildDraft for an
    // unedited change — so no eager seed is needed.
    return buildDraft(change)[field] || '';
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
          <div className="mt-1 text-[11px] text-slate-500">
            HTML allowed (e.g. {'<b>'}, {'<i>'}, {'<a>'})
          </div>
        </div>
        <div><SaveBtn /></div>
      </div>
    );
  }

  return null;
}

// One change-group card (all queued changes for a single Zotero item). The
// keyboard-active card takes a focus ref + ring; the others render plain.
export function ChangeGroup({
  group,
  isActive,
  groupRef,
  isPending,
  selected,
  toggleOne,
  drafts,
  setDrafts,
  flatCollections,
  saving,
  onSaveEdit,
  onRetry,
}) {
  return (
    <div
      ref={isActive ? groupRef : null}
      tabIndex={-1}
      className={`mb-3 border rounded-xl bg-white overflow-hidden outline-none ${
        isActive
          ? 'border-teal-400 ring-2 ring-teal-200'
          : 'border-slate-200'
      }`}
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
                  <ActionBadge tone={changeBadgeTone(change.change_type)}>
                    {changeTypeLabel(change.change_type)}
                  </ActionBadge>
                  <ActionBadge tone="slate">
                    status: {change.status || 'pending'}
                  </ActionBadge>
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
                {/* Retry is the one affordance for a FAILED row — re-apply via the
                    same writer path without re-queuing (Hick's Law: one button). */}
                {change.status === 'failed' && onRetry && (
                  <button
                    type="button"
                    onClick={() => onRetry(change)}
                    className="mt-1.5 px-2 py-1 rounded text-[11px] font-semibold bg-rose-600 text-white hover:bg-rose-700"
                  >
                    Retry
                  </button>
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
                onSave={() => onSaveEdit(change)}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
