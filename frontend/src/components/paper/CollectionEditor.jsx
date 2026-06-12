import { useEffect, useRef, useState } from 'react';
import { updateItemCollections } from '../../api/libraryApi.js';

// The user files most papers into their "Read Next" reading list, so the picker
// pre-selects it by default (one click "Add"). Matched by name — robust to the
// collection's key/parent — covering the common phrasings. Stable on purpose:
// NOT "remember last used", because one off-case filing would otherwise hijack
// the default the user relies on every day.
const READ_NEXT_RE = /read[\s_-]*next|read[\s_-]*later|to[\s_-]*read|reading[\s_-]*list/i;

// Default target among the ADDABLE collections (current memberships excluded):
// a Read-Next-named collection first; else the last collection the user added
// to (covers users without a reading list); else nothing pre-selected.
function preferredKey(addable) {
  const named = addable.find((c) => READ_NEXT_RE.test(c.name));
  if (named) return named.key;
  const last = localStorage.getItem('zs:lastCollectionKey');
  return addable.some((c) => c.key === last) ? last : '';
}

// Per-paper Zotero collection editor. Shows the item's current collections as
// removable chips + a picker to file it into another (e.g. "Read next"), so a
// paper you found via Meaning search lands in the right collection WITHOUT
// leaving the app and re-finding it in Zotero (Jakob's Law — matches Zotero's
// per-item "Add to Collection"; Working Memory — no app-switch). Reuses the
// existing backup-first POST .../collections write + the same force handshake
// as the syncs. `current` = [{key,name,path}] from the detail payload;
// `collections` = the flat [{key,name,depth}] list Library already loads.
export default function CollectionEditor({ itemKey, current = [], collections = [], onChanged }) {
  const [target, setTarget] = useState('');
  const [busyKey, setBusyKey] = useState('');
  const [err, setErr] = useState(null);
  // Stop re-seeding the default once the user deliberately picks a collection.
  const touchedRef = useRef(false);

  const currentKeys = new Set(current.map((c) => c.key));
  const addable = collections.filter((c) => !currentKeys.has(c.key));

  // Seed (and re-seed) the default when the addable set settles — collections
  // and the item's current memberships both load async — UNLESS the user has
  // already chosen, or their choice is still valid.
  useEffect(() => {
    if (touchedRef.current) return;
    if (target && addable.some((c) => c.key === target)) return;
    setTarget(preferredKey(addable));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collections, current]);

  // One write path for both add and remove; reused by the force-confirm retry.
  async function run({ add = [], remove = [], force = false }, busy) {
    setErr(null);
    setBusyKey(busy);
    try {
      const data = await updateItemCollections(itemKey, { add, remove, force });
      if (data?.requires_force) {
        setBusyKey('');
        if (window.confirm('Zotero appears to be running. Change collections anyway? (a backup is taken first)')) {
          return run({ add, remove, force: true }, busy);
        }
        return;
      }
      // Remember the last collection added to — the fallback default for users
      // without a Read-Next list (a named match still wins when present).
      if (add.length) localStorage.setItem('zs:lastCollectionKey', add[0]);
      touchedRef.current = false;  // let the default re-seed after the refetch
      setTarget('');
      onChanged?.();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusyKey('');
    }
  }

  return (
    <div className="text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] uppercase tracking-wider font-semibold text-slate-500">In</span>
        {current.length === 0 && <span className="text-slate-400">no collections yet</span>}
        {current.map((c) => (
          <span
            key={c.key}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-100 border border-slate-200 text-slate-700"
            title={c.path || c.name}
          >
            {c.name}
            <button
              type="button"
              onClick={() => run({ remove: [c.key] }, c.key)}
              disabled={!!busyKey}
              className="text-slate-400 hover:text-rose-600 disabled:opacity-50 leading-none"
              title={`Remove from ${c.name}`}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        <select
          value={target}
          onChange={(e) => { touchedRef.current = true; setTarget(e.target.value); }}
          className="max-w-[220px] px-1.5 py-0.5 rounded-md border border-slate-300 text-slate-700 bg-white"
          title="File this paper into a Zotero collection (defaults to your Read Next reading list)"
        >
          <option value="">Add to collection…</option>
          {addable.map((c) => (
            <option key={c.key} value={c.key}>
              {`${' '.repeat(c.depth * 2)}${c.name}`}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => run({ add: [target] }, target)}
          disabled={!target || !!busyKey}
          className="px-2 py-0.5 rounded-md bg-slate-700 text-white font-semibold hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"
        >
          {busyKey && busyKey === target ? 'Adding…' : 'Add'}
        </button>
      </div>
      {err && <p className="mt-1 text-rose-600">{err}</p>}
    </div>
  );
}
