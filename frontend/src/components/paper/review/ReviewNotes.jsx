import { useEffect, useState } from 'react';
import { saveReviewNote } from '../../../api/goldenApi.js';

// Free-text "my notes" per paper — the Zotero-style jot space while reviewing.
// Local save always succeeds; the Zotero mirror is best-effort (refuses while
// Zotero is open) and surfaced as a soft status, never an error banner — the
// same soft-mirror UX the verdict note uses. Prefilled from detail.user_note.
//   onSaved     — called after a successful save so the parent can refetch the
//                 detail (keeps the cached user_note fresh; flips dirty→clean).
//   onDirtyChange — reports unsaved-edit state so the parent can guard navigation
//                 (Prev/Next/j/k unmount this component, losing an unsaved draft).
export default function ReviewNotes({ itemKey, initialNote = '', onSaved = () => {}, onDirtyChange = () => {} }) {
  const [note, setNote] = useState(initialNote);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(null); // { tone: 'ok'|'soft'|'err', text }

  // Sync the textarea to the fetched note when it changes (e.g. the post-save
  // refetch) WITHOUT clearing the status — otherwise the "Saved" confirmation
  // would flash and vanish the instant onSaved's refetch lands.
  useEffect(() => { setNote(initialNote); }, [initialNote]);
  // A genuinely new paper: reset the transient status (nav also remounts this
  // component, so this is belt-and-suspenders for a same-instance key change).
  useEffect(() => { setStatus(null); }, [itemKey]);

  const dirty = note !== initialNote;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);

  async function handleSave() {
    setSaving(true);
    setStatus(null);
    try {
      const res = await saveReviewNote({ item_key: itemKey, note });
      if (res.saved_offline) {
        setStatus({ tone: 'soft', text: 'Saved on this device — waiting to sync.' });
      } else if (res.note_written) {
        setStatus({ tone: 'ok', text: 'Saved to the app and Zotero.' });
      } else if (res.note_error) {
        setStatus({ tone: 'soft', text: 'Saved in the app — close Zotero to sync it there.' });
      } else {
        setStatus({ tone: 'ok', text: 'Saved in the app.' });
      }
      onSaved(); // refetch detail so the cached note is fresh and dirty→clean
    } catch (err) {
      setStatus({ tone: 'err', text: `Save failed: ${err.message || err}` });
    } finally {
      setSaving(false);
    }
  }

  const toneCls = {
    ok: 'text-emerald-700',
    soft: 'text-amber-700',
    err: 'text-rose-700',
  };

  return (
    <div>
      <h3 className="text-sm font-bold text-slate-900 mb-3">My notes</h3>
      <textarea
        rows={5}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Jot your thoughts while reviewing… (saved here and mirrored to Zotero)"
        className="w-full text-sm px-3 py-2 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-teal-500"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        {status ? (
          <span className={`text-[12px] ${toneCls[status.tone]}`}>{status.text}</span>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !dirty}
          className="px-4 py-1.5 rounded-lg bg-teal-700 text-white text-sm font-semibold hover:bg-teal-800 disabled:bg-slate-200 disabled:text-slate-500"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
