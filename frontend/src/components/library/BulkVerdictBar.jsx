import { useState } from 'react';
import { updateItemTags } from '../../api/libraryApi.js';
import { decide } from '../../utils/verdictUndo.js';
import { pretty } from '../../utils/priorityLabels.js';

// Bulk verdict + "mark read" for the Read-next selection bar (L-11): verdicts go through
// utils/verdictUndo like the detail's pills (surface library_bulk; dont_read queues its ❌ tag after the
// Undo window) and the page shows ONE Undo for the batch (`onVerdicts`); 👀 "skimmed" is the engagement
// tag that moves a paper out of the queue. Applied one by one so a failure reports how many landed.
const VERDICTS = ['must_read', 'should_read', 'could_read', 'dont_read'];
const BTN = 'px-2 py-0.5 rounded-md border border-slate-300 bg-white text-[11px] text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed';

export default function BulkVerdictBar({ keys, disabled, onDone, onVerdicts, priorOf }) {
  const [busy, setBusy] = useState(false);
  const n = keys.length;

  async function apply(label, confirmText, write, landed) {
    if (!n || (confirmText && !window.confirm(confirmText))) return;
    setBusy(true);
    let done = 0;
    try {
      for (const key of keys) {
        await write(key);
        done += 1;
      }
      if (landed) { landed(done); onDone('', false, true, false); } else onDone(`${label}: ${done} paper${done === 1 ? '' : 's'}.`, false);
    } catch (err) {
      landed?.(done);
      onDone(`${label} stopped after ${done} of ${n}: ${err.message || err}`, true, done > 0);
    } finally {
      setBusy(false);
    }
  }

  const verdict = (priority) => {
    const batch = [];
    return apply(
      `Verdict “${pretty(priority)}”`,
      priority === 'dont_read' ? `Mark ${n} paper${n === 1 ? '' : 's'} Not interested? Each gets a ❌ tag queued for Zotero.` : '',
      async (key) => {
        const handle = decide(key, priority, { prior: priorOf(key), surface: 'library_bulk', keepOpen: batch.length > 0 });
        batch.push({ key, handle });
        await handle.done;
      },
      (landed) => onVerdicts({ priority, batch: batch.slice(0, landed) }),
    );
  };

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
