import { useEffect, useState } from 'react';
import { PRIORITY_LABELS, pretty } from '../utils/priorityLabels.js';

// Verdict editor for a single paper.
// Props:
//   itemKey: string
//   derivedPriority: string   — the model's guess. FORK-A: NOT used to preselect
//                               (the verdict is the model's own training ground
//                               truth, so the human picks independently); kept in
//                               the signature for caller compatibility.
//   existingVerdict: { id, item_key, user_priority, comment, created_at } | null
//   onSubmit: ({ user_priority, comment }) => void
//   onDelete: () => void
//   submitting / submitError / deleting / deleteError

// One human vocabulary for the verdict (Mental Model / Jakob's Law): the buttons
// read in plain words, not the raw `must_read` enum. The loud SOLID-fill `cls` is
// the sanctioned Von Restorff accent for the verdict surface (kept local).
export const PRIORITIES = [
  { key: 'must_read', cls: 'bg-emerald-600 hover:bg-emerald-700 text-white' },
  { key: 'should_read', cls: 'bg-sky-600 hover:bg-sky-700 text-white' },
  { key: 'could_read', cls: 'bg-amber-500 hover:bg-amber-600 text-white' },
  { key: 'dont_read', cls: 'bg-rose-600 hover:bg-rose-700 text-white' },
].map((p) => ({ ...p, label: PRIORITY_LABELS[p.key] }));

// Text-colour per priority so the saved-verdict word reads its own tone — fixes
// the bug where every "Previously" value was hardcoded emerald (a saved Remove ❌
// rendered green). Pretty label + correct colour, not the raw enum.
const PRIORITY_TEXT = {
  must_read: 'text-emerald-700',
  should_read: 'text-sky-700',
  could_read: 'text-amber-700',
  dont_read: 'text-rose-700',
};

export default function VerdictPanel({
  itemKey,
  derivedPriority = null, // eslint-disable-line no-unused-vars -- see FORK-A note above
  existingVerdict = null,
  onSubmit = () => {},
  onDelete = () => {},
  submitting = false,
  submitError = null,
  deleting = false,
  deleteError = null,
}) {
  const [priority, setPriority] = useState(existingVerdict?.user_priority ?? null);
  const [comment, setComment] = useState(existingVerdict?.comment ?? '');
  const [editing, setEditing] = useState(!existingVerdict);

  // Reset when a different paper is selected or the verdict changes (e.g. after a
  // successful mutation invalidates the query).
  useEffect(() => {
    setPriority(existingVerdict?.user_priority ?? null);
    setComment(existingVerdict?.comment ?? '');
    setEditing(!existingVerdict);
  }, [itemKey, existingVerdict?.id, existingVerdict?.user_priority, existingVerdict?.comment]);

  function handleSave() {
    if (!priority) return;
    onSubmit({ user_priority: priority, comment });
  }

  function handleCancel() {
    setPriority(existingVerdict?.user_priority ?? null);
    setComment(existingVerdict?.comment ?? '');
    if (existingVerdict) setEditing(false);
  }

  function handleDelete() {
    if (!existingVerdict) return;
    const ok = window.confirm(
      `Delete your verdict (${pretty(existingVerdict.user_priority)}) for this paper?`,
    );
    if (ok) onDelete();
  }

  // Resting read state: a saved verdict shows ONLY as a hairline "Previously" row
  // (state, not chrome) with Edit / Delete. The button grid + textarea appear on
  // Edit — three echoes of the saved verdict collapse to one.
  if (existingVerdict && !editing) {
    return (
      <div>
        <h3 className="text-sm font-bold text-slate-900 mb-3">Your verdict</h3>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-slate-600">
          <span>
            Previously:{' '}
            <span className={`font-semibold ${PRIORITY_TEXT[existingVerdict.user_priority] || 'text-slate-700'}`}>
              {pretty(existingVerdict.user_priority)}
            </span>
            {existingVerdict.created_at && (
              <span className="text-slate-400">{' '}· {existingVerdict.created_at}</span>
            )}
          </span>
          {existingVerdict.comment && (
            <span className="italic text-slate-500">“{existingVerdict.comment}”</span>
          )}
          <span className="ml-auto flex gap-1.5">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="px-2 py-0.5 rounded border border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              className="px-2 py-0.5 rounded border border-rose-300 bg-white text-rose-700 hover:bg-rose-100 disabled:opacity-50"
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </span>
        </div>
        {deleteError && (
          <div className="mt-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
            {deleteError}
          </div>
        )}
      </div>
    );
  }

  // Edit state (a new verdict, or editing an existing one). Cancel shows only when
  // there's something to discard — one stable footer shape, no duplicate Reset.
  const dirty = existingVerdict ? editing : Boolean(priority || comment);
  return (
    <div>
      <h3 className="text-sm font-bold text-slate-900 mb-3">Your verdict</h3>

      <div className="flex flex-wrap gap-2 mb-3">
        {PRIORITIES.map((p) => {
          const active = priority === p.key;
          return (
            <button
              key={p.key}
              type="button"
              onClick={() => setPriority(p.key)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors border ${
                active ? `${p.cls} border-transparent` : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-100'
              }`}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <textarea
        rows={4}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Why? (free text — used to detect patterns later)"
        className="w-full text-sm px-3 py-2 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-teal-500"
      />

      {(submitError || deleteError) && (
        <div className="mt-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
          {submitError || deleteError}
        </div>
      )}

      <div className="mt-3 flex justify-end gap-2">
        {dirty && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={submitting}
            className="px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-semibold hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          onClick={handleSave}
          disabled={!priority || submitting}
          className="px-4 py-1.5 rounded-lg bg-teal-700 text-white text-sm font-semibold hover:bg-teal-800 disabled:bg-slate-200 disabled:text-slate-500"
        >
          {submitting ? 'Saving…' : existingVerdict ? 'Update' : 'Save verdict'}
        </button>
      </div>
    </div>
  );
}
