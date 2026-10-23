import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { publishStatus, resolveConflict, syncStatusEvent } from '../offlineStore.js';
import { syncNow } from '../syncClient.js';
import { pretty } from '../utils/priorityLabels.js';
import Button from './ui/Button.jsx';

function conflictValue(field, value, operation, comment) {
  const text = value === undefined ? '(not supplied)'
    : operation === 'delete' || value === null ? '(cleared)'
      : field === 'verdict' ? pretty(value)
        : field === 'review_question' ? value.question ?? '(question not supplied)' : String(value);
  return `${text}${comment ? `\n\nComment:\n${comment}` : ''}`;
}

function ConnectionGuide() {
  const [shellReady, setShellReady] = useState(Boolean(navigator.serviceWorker?.controller));
  useEffect(() => {
    const update = () => setShellReady(Boolean(navigator.serviceWorker?.controller));
    navigator.serviceWorker?.addEventListener('controllerchange', update);
    return () => navigator.serviceWorker?.removeEventListener('controllerchange', update);
  }, []);
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-slate-600">Offline use &amp; installation</summary>
      <div className="mt-2 space-y-2 text-slate-600 max-w-prose">
        <p>{!window.isSecureContext
          ? 'This connection cannot install an offline app. Use a trusted secure connection; do not bypass certificate warnings.'
          : shellReady ? 'Offline shell is active. Test a reload with the server off before relying on it.'
            : 'Offline shell is not active yet. Open the app online and reload after the shell is installed.'}</p>
        <ol className="list-decimal pl-5 space-y-1">
          <li>While connected, sync and open the papers you need to cache their review context.</li>
          <li>On iPhone/iPad Safari, use Share → Add to Home Screen. On desktop, use the browser’s Install app action if offered.</li>
          <li>With the Mac off, reopen Library to read cached papers and explicitly save verdicts and notes. Keep this browser’s site data; clearing it removes unsynced changes.</li>
          <li>When the Mac is back, return to this same app address and choose Sync now. Resolve any Changed elsewhere notices.</li>
        </ol>
        <p>The current server accepts same-machine loopback only. An iPhone/iPad cannot connect to the Mac’s localhost. Remote mobile use needs a separately supported authenticated HTTPS deployment; exposing a LAN port or bypassing origin/TLS guards is not a setup step.</p>
        <p>PDFs, figures, AI answers, acquisition and rescoring are not synced. An app sync acknowledgement does not prove Zotero or another device received your changes.</p>
      </div>
    </details>
  );
}

export default function OfflineStatus() {
  const [sync, setSync] = useState({ online: navigator.onLine, connection: 'unchecked', pending: null, conflicts: [], rejected: [] });
  const [resolving, setResolving] = useState(null);
  const [resolutionError, setResolutionError] = useState('');
  useEffect(() => {
    const update = (event) => setSync(event.detail);
    window.addEventListener(syncStatusEvent, update);
    publishStatus();
    return () => window.removeEventListener(syncStatusEvent, update);
  }, []);
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
  const unavailable = !sync.online || sync.connection === 'unavailable';
  const headline = !sync.online ? 'Offline'
    : sync.connection === 'syncing' ? 'Syncing with the app…'
      : sync.connection === 'connected' ? 'Connected to the app' : 'Connection not confirmed';
  return (
    <section aria-label="Offline sync" className="mt-2 min-w-0 text-xs text-slate-600">
      <div className="flex flex-wrap items-center gap-2">
        <p role="status" aria-live="polite">
          {headline} · {sync.pending == null ? 'pending count unavailable' : `${sync.pending} pending`}
          {sync.paper_count != null && ` · ${sync.paper_count} cached papers`}
        </p>
        <Button size="sm" variant="secondary" disabled={!sync.online || sync.connection === 'syncing'} onClick={() => syncNow()}>
          {sync.connection === 'syncing' ? 'Syncing…' : 'Sync now'}
        </Button>
      </div>
      {sync.last_synced_at && <p className="mt-1">Last app sync: {new Date(sync.last_synced_at).toLocaleString()}</p>}
      {sync.sync_error && <p role="status" className="mt-1 text-amber-800">{sync.sync_error}. Device changes are retained. Start the Mac app at this same address, then retry Sync now.</p>}
      {unavailable && <p className="mt-1">Cached papers, verdicts, and notes work; PDFs, AI, and rescore are unavailable. Open Library for the cached working set.</p>}
      {sync.local_error && <p role="alert" className="mt-1 text-rose-700">Device storage unavailable: {sync.local_error}. Offline saves cannot be confirmed. Do not clear site data; keep unsaved text until storage works.</p>}
      {resolutionError && <p role="alert" className="mt-1 text-rose-700">{resolutionError}</p>}
      {(sync.conflicts || []).map((conflict) => (
        <details key={conflict.mutation_id} open className="mt-2 border border-amber-200 rounded-lg p-2">
          <summary className="cursor-pointer">Changed elsewhere — compare before resolving</summary>
          <span className="break-words">Sync conflict for <span className="break-all">{conflict.item_key}</span> ({conflict.field}).</span>
          <div className="grid sm:grid-cols-2 gap-2 my-2">
            <label>Server
              <textarea readOnly rows={4} className="block w-full border p-2" value={conflictValue(conflict.field, conflict.canonical?.value, 'set', conflict.canonical?.comment)} />
            </label>
            <label>This device
              <textarea readOnly rows={4} className="block w-full border p-2" value={conflictValue(conflict.field, conflict.value, conflict.operation, conflict.comment)} />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={resolving !== null} onClick={() => resolve(conflict.mutation_id, true)}>Keep mine</Button>
            <Button size="sm" variant="secondary" disabled={resolving !== null} onClick={() => resolve(conflict.mutation_id, false)}>Use server</Button>
            <NavLink className="underline self-center" to={`/paper/${encodeURIComponent(conflict.item_key)}`}>Open paper</NavLink>
          </div>
          <p className="mt-1">Either choice saves a new auditable resolution on this device, then syncs when reachable.</p>
        </details>
      ))}
      {sync.rejected?.length > 0 && (
        <details className="mt-2">
          <summary>{sync.rejected.length} rejected draft(s) preserved on this device. Other valid changes can still sync.</summary>
          {sync.rejected.map((draft) => (
            <div key={draft.mutation_id} className="mt-2">
              <p className="break-words"><span className="break-all">{draft.item_key}</span> ({draft.field}): {draft.error}.</p>
              <label>
                Saved draft — copy this text, correct it and save again on the paper page.
                <textarea readOnly rows={4} className="block w-full border p-2"
                  value={`${draft.field === 'review_question' ? draft.value?.question ?? '' : draft.value ?? ''}${draft.comment ? `\n\nComment:\n${draft.comment}` : ''}`} />
              </label>
              <NavLink className="underline" to={`/paper/${encodeURIComponent(draft.item_key)}`}>Open paper</NavLink>
            </div>
          ))}
        </details>
      )}
      <ConnectionGuide />
    </section>
  );
}
