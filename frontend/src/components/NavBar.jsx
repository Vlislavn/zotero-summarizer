import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { publishStatus, resolveConflict, syncStatusEvent } from '../offlineStore.js';
import { syncNow } from '../syncClient.js';

// Primary tabs: Library / Today / Settings / Ops (Increment 3 nav collapse —
// 8 routes folded to 3 daily surfaces + one Ops surface). Library (Read next) is
// the landing surface and the leftmost "home" tab (Serial Position / Jakob's
// Law: the default view IS the first tab), since it carries both daily workflows
// (Read-next queue + Meaning search) AND the folded-in Batch-label mode (the
// former Annotate page). Today (feed cull) sits next, then Settings. Ops is the
// rarely-used operator surface (Feed review + Triage jobs + Pending changes) on
// its own tab — Hick's Law: the bar stays at four flat choices, no disclosure.
// Search (Targeted Search) sits next to Library: both are research entry points —
// Library is the *push* surface (cull what feeds brought), Search is the *pull*
// surface (go find papers on a topic). Kept a flat choice, no disclosure.
const PRIMARY = [
  { to: '/library', label: 'Library' },
  { to: '/search', label: 'Search' },
  { to: '/today', label: 'Today' },
  { to: '/settings', label: 'Settings' },
  { to: '/ops', label: 'Ops' },
];

function tabClass({ isActive }) {
  return [
    'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
    isActive
      ? 'bg-forest-800 text-white'
      : 'text-slate-700 hover:bg-slate-200',
  ].join(' ');
}

export default function NavBar() {
  const [sync, setSync] = useState({ online: navigator.onLine, pending: 0, conflicts: [], rejected: [], message: '' });
  useEffect(() => {
    const update = (event) => setSync(event.detail);
    window.addEventListener(syncStatusEvent, update);
    publishStatus();
    return () => window.removeEventListener(syncStatusEvent, update);
  }, []);
  async function resolve(id, keepLocal) {
    await resolveConflict(id, keepLocal);
    syncNow();
  }
  const conflict = sync.conflicts[0];
  const rejected = sync.rejected[0];
  return (
    <header className="glass border border-slate-200 rounded-2xl p-4 mb-5 overflow-visible relative z-30">
      <div className="flex items-center gap-4 flex-wrap">
        <h1 className="font-display text-2xl font-light text-slate-900">Zotero Summarizer</h1>
        <nav className="flex gap-1.5 items-center">
          {PRIMARY.map((t) => (
            <NavLink key={t.to} to={t.to} className={tabClass}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className={`mt-2 text-xs ${conflict ? 'text-rose-700' : sync.online ? 'text-slate-500' : 'text-amber-800'}`}>
        {conflict ? (
          <span>
            Sync conflict for {conflict.item_key} ({conflict.field}).{' '}
            <button className="underline font-semibold" onClick={() => resolve(conflict.mutation_id, true)}>Keep mine</button>
            {' · '}
            <button className="underline font-semibold" onClick={() => resolve(conflict.mutation_id, false)}>Use server</button>
            {sync.conflicts.length > 1 && ` · ${sync.conflicts.length - 1} more`}
          </span>
        ) : rejected ? (
          <span>Sync rejected for {rejected.item_key} ({rejected.field}): {rejected.error}. The device copy was preserved; refresh the app before retrying.</span>
        ) : sync.online ? (
          <span>{sync.pending
            ? `${sync.pending} change${sync.pending === 1 ? '' : 's'} waiting to sync`
            : sync.message === 'Server unavailable'
              ? 'Server unavailable · cached papers, verdicts, and notes work; PDFs, AI, and rescore are unavailable.'
              : (sync.message || 'Synced')}</span>
        ) : (
          <span>Offline · {sync.pending} pending · cached papers, verdicts, and notes work; PDFs, AI, and rescore are unavailable.</span>
        )}
      </div>
    </header>
  );
}
