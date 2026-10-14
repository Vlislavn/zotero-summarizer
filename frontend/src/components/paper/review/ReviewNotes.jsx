import { useEffect, useRef, useState } from 'react';
import { saveReviewNote } from '../../../api/goldenApi.js';
import { noteDeliveryText } from '../../../hooks/useNoteDelivery.js';

// Free-text "my notes" per paper — the Zotero-style jot space while reviewing.
// The Zotero mirror is best-effort (refuses while
// Zotero is open) and surfaced as a soft status, never an error banner — the
// same soft-mirror UX the verdict note uses. Prefilled from detail.user_note.
//   onSaved     — called after a successful save so the parent can refetch the
//                 detail (keeps the cached user_note fresh; flips dirty→clean).
//   onDirtyChange — reports unsaved-edit state so the parent can guard navigation
//                 (Prev/Next/j/k unmount this component, losing an unsaved draft).
export default function ReviewNotes({ itemKey, initialNote = '', questions = [], questionMirror, onMirrorStart, onSaved, onDirtyChange }) {
  const [draft, setDraft] = useState({ itemKey, note: initialNote, baseline: initialNote });
  const { note, baseline } = draft;
  const keyRef = useRef(itemKey);
  keyRef.current = itemKey;
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(null); // { tone: 'ok'|'soft'|'err', text }

  // Sync the textarea to the fetched note when it changes (e.g. the post-save
  // refetch) WITHOUT clearing the status — otherwise the "Saved" confirmation
  // would flash and vanish the instant onSaved's refetch lands.
  useEffect(() => {
    setDraft((previous) => previous.itemKey !== itemKey || previous.note === previous.baseline
      ? { itemKey, note: initialNote, baseline: initialNote } : previous);
  }, [itemKey, initialNote]);
  // A genuinely new paper: reset the transient status (nav also remounts this
  // component, so this is belt-and-suspenders for a same-instance key change).
  useEffect(() => { setStatus(null); setSaving(false); }, [itemKey]);

  const dirty = note !== baseline;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  async function handleSave() {
    const savedKey = itemKey;
    const savedNote = note;
    setSaving(true);
    setStatus(null);
    const reportMirror = onMirrorStart?.({ note: savedNote });
    try {
      const res = await saveReviewNote({ item_key: savedKey, note: savedNote });
      if (keyRef.current !== savedKey) return;
      setDraft((previous) => ({ ...previous, baseline: savedNote }));
      reportMirror?.(res.saved_offline ? null : res);
      if (res.saved_offline) {
        setStatus({ tone: 'soft', text: 'Saved on this device — waiting to sync.' });
      } else if (res.note_written) {
        setStatus({ tone: 'ok', text: 'Saved to the app and Zotero.' });
      } else if (res.note_error) {
        setStatus({ tone: 'soft', text: 'Saved in the app — close Zotero to sync it there.' });
      } else {
        setStatus({ tone: 'ok', text: 'Saved in the app.' });
      }
      onSaved?.();
    } catch (err) {
      if (keyRef.current === savedKey) setStatus({ tone: 'err', text: `Save failed: ${err.message || err}` });
    } finally {
      if (keyRef.current === savedKey) setSaving(false);
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
      {questions.length > 0 && (
        <ul aria-label="Saved review questions" className="mb-3 space-y-2 text-sm text-slate-700">
          {questions.map((row) => (
            <li key={row.submission_id} className="break-words [overflow-wrap:anywhere]">
              <p>{row.question}</p>
              <span className="text-xs text-slate-500">
                {row.saved_offline
                  ? `Saved on this device — ${row.sync_status === 'rejected' ? 'sync rejected'
                    : row.sync_status === 'conflict' ? 'sync conflict' : 'waiting to sync'}`
                  : 'Saved question'}
              </span>
            </li>
          ))}
        </ul>
      )}
      {questions.length > 0 && <p className="mb-3 text-xs text-slate-500">{noteDeliveryText(questionMirror)}</p>}
      <textarea
        aria-label="Manual notes"
        rows={5}
        value={note}
        onChange={(e) => setDraft({ ...draft, note: e.target.value })}
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
          className="shrink-0 px-4 py-1.5 rounded-lg bg-teal-700 text-white text-sm font-semibold hover:bg-teal-800 disabled:bg-slate-200 disabled:text-slate-500"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
