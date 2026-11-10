import { useState } from 'react';
import { submitVerdict } from '../../api/goldenApi.js';
import { queueRejectTag, updateItemTags } from '../../api/libraryApi.js';
import { pretty } from '../../utils/priorityLabels.js';

// Bulk verdict + "mark read" for the Read-next selection bar (L-11): the same
// per-paper writes the row panel uses (submitVerdict; dont_read also queues the ❌
// tag; 👀 "skimmed" is the engagement tag that moves a paper out of the queue),
// applied one by one so a failure reports how many landed instead of hiding it.
const VERDICTS = ['must_read', 'should_read', 'could_read', 'dont_read'];
const BTN = 'px-2 py-0.5 rounded-md border border-slate-300 bg-white text-[11px] text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed';

export default function BulkVerdictBar({ keys, disabled, onDone }) {
  const [busy, setBusy] = useState(false);
  const n = keys.length;

  async function apply(label, confirmText, write) {
    if (!n || (confirmText && !window.confirm(confirmText))) return;
    setBusy(true);
    let done = 0;
    try {
      for (const key of keys) {
        await write(key);
        done += 1;
      }
      onDone(`${label}: ${done} paper${done === 1 ? '' : 's'}.`, false);
    } catch (err) {
      onDone(`${label} stopped after ${done} of ${n}: ${err.message || err}`, true, done > 0);
    } finally {
      setBusy(false);
    }
  }

  const verdict = (priority) => apply(
    `Verdict “${pretty(priority)}”`,
    priority === 'dont_read' ? `Remove ${n} paper${n === 1 ? '' : 's'}? Each gets a ❌ tag queued for Zotero.` : '',
    async (key) => {
      const result = await submitVerdict({ item_key: key, user_priority: priority });
      if (priority === 'dont_read' && !result?.saved_offline) await queueRejectTag(key);
    },
  );

  const markRead = () => apply('Marked read', '', async (key) => {
    let data = await updateItemTags(key, { addTags: ['👀'] });
    if (data?.requires_force) {
      if (!window.confirm('Zotero appears to be running. Change tags anyway? (a backup is taken first)')) throw new Error('cancelled');
      data = await updateItemTags(key, { addTags: ['👀'], force: true });
    }
    if (data?.requires_force) throw new Error('tag change not applied');
  });

  const off = disabled || busy || n === 0;
  return (
    <span className="inline-flex flex-wrap items-center gap-1" role="group" aria-label="Bulk verdict">
      <span className="text-[11px] text-slate-500">{busy ? 'Saving…' : `Set verdict (${n}):`}</span>
      {VERDICTS.map((priority) => (
        <button key={priority} type="button" disabled={off} className={BTN}
          aria-label={`Set ${pretty(priority)} for ${n} selected`} onClick={() => verdict(priority)}>
          {pretty(priority)}
        </button>
      ))}
      <button type="button" disabled={off} className={BTN} onClick={markRead}
        title="Adds the 👀 (skimmed) tag in Zotero — the papers leave Read next. Backup-first.">
        {`Mark read (${n})`}
      </button>
    </span>
  );
}
