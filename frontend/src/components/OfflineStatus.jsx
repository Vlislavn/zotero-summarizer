import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { allPapers, publishStatus, resolveConflict, syncStatusEvent } from '../offlineStore.js';
import { syncNow } from '../syncClient.js';
import { pretty } from '../utils/priorityLabels.js';
import usePendingCount from '../hooks/usePendingCount.js';
import ConnectionGuide from './ConnectionGuide.jsx';
import Button from './ui/Button.jsx';

function conflictValue(field, value, operation, comment) {
  const text = value === undefined ? '(not supplied)'
    : operation === 'delete' || value === null ? '(cleared)'
      : field === 'verdict' ? pretty(value) : String(value);
  return `${text}${comment ? `\n\nComment:\n${comment}` : ''}`;
}

const CONFLICT_WHAT = { review_note: 'note', verdict: 'verdict', review_question: 'question' };
const conflictSentence = (conflict, title) =>
  `Your ${CONFLICT_WHAT[conflict.field] || 'change'} on ${title ? `“${title}”` : 'a paper'} was changed elsewhere.`;

export default function OfflineStatus() {
  const [titles, setTitles] = useState({});
  const [sync, setSync] = useState({ online: navigator.onLine, connection: 'unchecked', pending: null, conflicts: [], rejected: [] });
  const [resolving, setResolving] = useState(null);
  const [open, setOpen] = useState(false);
  const [resolutionError, setResolutionError] = useState('');
  useEffect(() => {
    const update = (event) => setSync(event.detail);
    window.addEventListener(syncStatusEvent, update);
    publishStatus();
    return () => window.removeEventListener(syncStatusEvent, update);
  }, []);
  const conflictCount = sync.conflicts?.length || 0;
  useEffect(() => {
    if (!conflictCount) return;
    allPapers().then(rows => setTitles(Object.fromEntries(rows.map(row => [row.item_key, row.title])))).catch(() => {});
  }, [conflictCount]);
  async function resolve(id, keepLocal) {
    setResolving(id);
    setResolutionError('');
    try {
      await resolveConflict(id, keepLocal);
      await syncNow();
    } catch (error) {
      setResolutionError(`Resolution not saved: ${error.message || error}. Both versions are preserved; retry.`);
    } finally {
      setResolving(null);
    }
  }
  const { pathname } = useLocation();
  const down = !sync.online || sync.connection === 'unavailable';
  const queued = usePendingCount(!down, sync.connection, sync.last_synced_at, pathname);
  const attention = down || Boolean(sync.sync_error || sync.local_error || sync.conflicts?.length || sync.rejected?.length);
  const headline = !sync.online ? 'Offline'
    : sync.connection === 'unavailable' ? 'Server unreachable'
      : sync.connection === 'syncing' ? 'Syncing…'
        : sync.connection === 'connected' ? 'Connected' : 'Connection not confirmed';
  const unsent = sync.pending == null ? 'device storage unavailable' : sync.pending > 0 ? `${sync.pending} unsent on this device` : null;
  const line = [headline, queued == null ? null : `${queued} pending change${queued === 1 ? '' : 's'}`, unsent].filter(Boolean).join(' · ');
  const dot = down || sync.local_error ? 'bg-rose-600' : attention ? 'bg-amber-500' : sync.connection === 'connected' ? 'bg-emerald-600' : 'bg-slate-400';
  return (
    <section aria-label="Offline sync" className="min-w-0 text-xs text-slate-600">
      <div className="flex items-center gap-2 min-h-9 sm:min-h-11">
        <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
        <p role="status" aria-live="polite" className="min-w-0 flex-1 truncate">{line}</p>
        {!attention && (
          <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className={`no-tap-min relative min-h-9 sm:min-h-11 px-2 -mr-2 text-forest-800 font-medium after:absolute after:inset-x-0 after:top-0 sm:after:hidden ${open ? 'after:h-9' : 'after:h-11'}`}>
            {open ? 'Hide details' : 'Details'}
          </button>
        )}
      </div>
      {(open || attention) && <div className="pb-3 space-y-1">
        <div className="flex flex-wrap items-center gap-x-3">
          <Button size="sm" variant="secondary" className="min-h-11" disabled={!sync.online || sync.connection === 'syncing'} onClick={() => syncNow()}>
            {sync.connection === 'syncing' ? 'Syncing…' : 'Sync now'}
          </Button>
          {!down && <NavLink className="min-h-11 inline-flex items-center underline" to="/ops?tab=pending">Pending changes</NavLink>}
        </div>
        {sync.last_synced_at && <p>Last synced {new Date(sync.last_synced_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}{sync.paper_count != null && ` · ${sync.paper_count} papers on this device`}</p>}
        {down && <p className="text-amber-800">
          Server unreachable — reading only. Works: papers already synced to this device (open from Library; verdicts and notes are kept here and sent later).
          Not available: Today decisions, PDFs, AI, rescoring. Check that your Mac is on, the app is running for iPhone, and Tailscale is connected, then tap Sync now.
        </p>}
        {!down && sync.sync_error && <p role="status" className="text-amber-800">{sync.sync_error}. Changes made here stay on this device; retry Sync now.</p>}
        {sync.local_error && <p role="alert" className="text-rose-700">Device storage unavailable: {sync.local_error}. Offline saves cannot be confirmed. Do not clear site data; keep unsaved text until storage works.</p>}
      {resolutionError && <p role="alert" className="mt-1 text-rose-700">{resolutionError}</p>}
      {(sync.conflicts || []).map((conflict) => {
        const intent = conflict.operation === 'keep_server' ? conflict.original_intent : conflict;
        return (
        <details key={conflict.mutation_id} open className="mt-2 border border-amber-200 rounded-lg p-2">
          <summary className="cursor-pointer">Changed elsewhere — compare before resolving</summary>
          <span className="break-words">{conflictSentence(conflict, conflict.title || titles[conflict.item_key])}</span>
          <div className="grid sm:grid-cols-2 gap-2 my-2">
            <label>Server
              <textarea readOnly rows={4} className="block w-full border p-2" value={conflictValue(conflict.field, conflict.canonical?.value, 'set', conflict.canonical?.comment)} />
            </label>
            <label>This device
              <textarea readOnly rows={4} className="block w-full border p-2" value={conflictValue(conflict.field, intent?.value, intent?.operation, intent?.comment)} />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" className="min-h-11" disabled={resolving !== null} onClick={() => resolve(conflict.mutation_id, true)}>Keep mine</Button>
            <Button size="sm" variant="secondary" className="min-h-11" disabled={resolving !== null} onClick={() => resolve(conflict.mutation_id, false)}>Use server</Button>
            <NavLink className="underline self-center" to={`/paper/${encodeURIComponent(conflict.item_key)}`}>Open paper</NavLink>
          </div>
          <p className="mt-1">Either choice saves a new auditable resolution on this device, then syncs when reachable.</p>
        </details>
        );
      })}
      {sync.rejected?.length > 0 && (
        <details className="mt-2">
          <summary>{sync.rejected.length} rejected draft(s) preserved on this device. Other valid changes can still sync.</summary>
          {sync.rejected.map((draft) => {
            const intent = draft.original_intent || draft;
            return (
            <div key={draft.mutation_id} className="mt-2">
              <p className="break-words"><span className="break-all">{draft.item_key}</span> ({draft.field}): {draft.error}.</p>
              <label>
                Saved draft — copy this text, correct it and save again on the paper page.
                <textarea readOnly rows={4} className="block w-full border p-2" value={`${intent.value ?? ''}${intent.comment ? `\n\nComment:\n${intent.comment}` : ''}`} />
              </label>
              <NavLink className="underline" to={`/paper/${encodeURIComponent(draft.item_key)}`}>Open paper</NavLink>
            </div>
            );
          })}
        </details>
      )}
      <ConnectionGuide />
      </div>}
    </section>
  );
}
