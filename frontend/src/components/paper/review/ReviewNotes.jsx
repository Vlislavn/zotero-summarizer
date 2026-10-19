import { useEffect, useRef, useState } from 'react';
import { saveReviewNote } from '../../../api/goldenApi.js';

export default function ReviewNotes({ itemKey, initialNote = '', onSaved, onDirtyChange }) {
  const [draft, setDraft] = useState({ itemKey, note: initialNote, baseline: initialNote });
  const { note, baseline } = draft;
  const pendingSave = useRef(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    setDraft((previous) => previous.itemKey !== itemKey || previous.note === previous.baseline
      ? { itemKey, note: initialNote, baseline: initialNote } : previous);
  }, [itemKey, initialNote]);
  useEffect(() => {
    setStatus(null);
    setSaving(false);
    return () => { pendingSave.current = null; };
  }, [itemKey]);

  const dirty = note !== baseline;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  async function handleSave() {
    if (pendingSave.current) return;
    const request = {};
    pendingSave.current = request;
    const current = () => pendingSave.current === request;
    const savedKey = itemKey;
    const savedNote = note;
    setSaving(true);
    setStatus(null);
    try {
      const res = await saveReviewNote({ item_key: savedKey, note: savedNote });
      if (!current()) return;
      setDraft((previous) => ({ ...previous, baseline: savedNote }));
      if (res.saved_offline) {
        setStatus({ tone: 'soft', text: 'Saved on this device.' });
      } else if (res.note_written) {
        setStatus({ tone: 'ok', text: 'Saved to the app and Zotero.' });
      } else if (res.note_error) {
        setStatus({ tone: 'soft', text: 'Saved in the app — Zotero delivery is not confirmed.' });
      } else {
        setStatus({ tone: 'ok', text: 'Saved in the app.' });
      }
      onSaved?.();
    } catch (err) {
      if (current()) setStatus({ tone: 'err', text: `Save failed: ${err.message || err}` });
    } finally {
      if (current()) { pendingSave.current = null; setSaving(false); }
    }
  }

  const toneCls = {
    ok: 'text-emerald-700',
    soft: 'text-amber-700',
    err: 'text-rose-700',
  };

  return (
    <div>
      <h3 className="text-sm font-medium text-slate-900 mb-3">My notes</h3>
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
        ) : <span />}
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !dirty}
          className="shrink-0 px-4 py-1.5 rounded-lg bg-teal-700 text-white text-sm font-medium hover:bg-teal-800 disabled:bg-slate-200 disabled:text-slate-500"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
