import { useEffect, useState } from 'react';

// Verdict editor for a single paper.
// Props:
//   itemKey: string
//   derivedPriority: string
//   existingVerdict: { id, item_key, user_priority, comment, created_at } | null
//   onSubmit: ({ user_priority, comment }) => void
//   onDelete: () => void
//   submitting: boolean
//   submitError: string | null
//   deleting: boolean
//   deleteError: string | null

const PRIORITIES = [
  { key: 'must_read', label: 'must_read', cls: 'bg-emerald-600 hover:bg-emerald-700 text-white' },
  { key: 'should_read', label: 'should_read', cls: 'bg-sky-600 hover:bg-sky-700 text-white' },
  { key: 'could_read', label: 'could_read', cls: 'bg-amber-500 hover:bg-amber-600 text-white' },
  { key: 'dont_read', label: 'dont_read', cls: 'bg-rose-600 hover:bg-rose-700 text-white' },
];

export default function VerdictPanel({
  itemKey,
  derivedPriority = null,
  existingVerdict = null,
  onSubmit = () => {},
  onDelete = () => {},
  submitting = false,
  submitError = null,
  deleting = false,
  deleteError = null,
}) {
  const initialPriority = existingVerdict?.user_priority ?? null;
  const initialComment = existingVerdict?.comment ?? '';
  const [priority, setPriority] = useState(initialPriority);
  const [comment, setComment] = useState(initialComment);
  const [editing, setEditing] = useState(!existingVerdict);

  // When a different paper is selected or the verdict changes (e.g. after a
  // successful mutation invalidates the query), reset the form to match.
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
      `Delete your verdict (${existingVerdict.user_priority}) for this paper?`,
    );
    if (ok) onDelete();
  }

  return (
    <div>
      <h3 className="text-sm font-bold text-slate-900 mb-3">
        Your verdict
        <span className="ml-2 text-[10px] uppercase tracking-wider text-slate-400 font-medium">
          1 must · 2 should · 3 could · 4 don't · j/k navigate
        </span>
      </h3>

      {existingVerdict && !editing && (
        <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900 flex flex-wrap items-center gap-2">
          <span>
            Previously:{' '}
            <span className="font-semibold">{existingVerdict.user_priority}</span>
            {existingVerdict.created_at && (
              <span className="text-emerald-700">
                {' '}· {existingVerdict.created_at}
              </span>
            )}
          </span>
          {existingVerdict.comment && (
            <span className="italic text-emerald-800">“{existingVerdict.comment}”</span>
          )}
          <span className="ml-auto flex gap-1.5">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="px-2 py-0.5 rounded border border-emerald-300 bg-white text-emerald-800 hover:bg-emerald-100"
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
      )}

      <div className="flex flex-wrap gap-2 mb-3">
        {PRIORITIES.map((p) => {
          const isUserVerdict = existingVerdict?.user_priority === p.key;
          const isDerived = derivedPriority === p.key;
          const active = priority === p.key;
          const disabled = existingVerdict && !editing;
          return (
            <div key={p.key} className="flex flex-col items-center gap-0.5">
              <button
                type="button"
                disabled={disabled}
                onClick={() => setPriority(p.key)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-60 disabled:cursor-not-allowed border ${
                  active
                    ? `${p.cls} border-transparent`
                    : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-100'
                }`}
              >
                {p.label}
              </button>
              <div className="flex items-center gap-1 h-3">
                {isDerived && (
                  <span className="text-[9px] uppercase tracking-wider text-slate-500">
                    derived
                  </span>
                )}
                {isUserVerdict && (
                  <span className="text-[9px] uppercase tracking-wider text-emerald-700 font-semibold">
                    yours
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <textarea
        rows={4}
        disabled={existingVerdict && !editing}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Why? (free text — used to detect patterns later)"
        className="w-full text-sm px-3 py-2 rounded-lg border border-slate-300 disabled:bg-slate-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-teal-500"
      />

      {(submitError || deleteError) && (
        <div className="mt-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
          {submitError || deleteError}
        </div>
      )}

      <div className="mt-3 flex justify-end gap-2">
        {existingVerdict && editing && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={submitting}
            className="px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-semibold hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel
          </button>
        )}
        {!existingVerdict && (priority || comment) && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={submitting}
            className="px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-slate-700 text-sm font-semibold hover:bg-slate-100 disabled:opacity-50"
          >
            Reset
          </button>
        )}
        <button
          type="button"
          onClick={handleSave}
          disabled={
            !priority ||
            submitting ||
            (existingVerdict && !editing)
          }
          className="px-4 py-1.5 rounded-lg bg-teal-700 text-white text-sm font-semibold hover:bg-teal-800 disabled:bg-slate-200 disabled:text-slate-500"
        >
          {submitting ? 'Saving…' : existingVerdict ? 'Update' : 'Save verdict'}
        </button>
      </div>
    </div>
  );
}
