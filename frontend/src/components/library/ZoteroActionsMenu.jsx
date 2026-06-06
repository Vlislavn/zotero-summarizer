import { useEffect, useRef } from 'react';

// Grouped "write to Zotero" actions for the Library toolbar.
//   • Hick's Law          — the three heavy, occasional whole-library WRITE
//                           operations no longer crowd the primary search row;
//                           the top toolbar drops from ~7 choices to ~4.
//   • Miller's Law / Law of Common Region — they're one labelled cluster, not
//                           three loose buttons mixed in with search.
//   • Jakob's Law         — a native <details> disclosure, the same idiom the
//                           page already uses for "Advanced filters".
//   • Tesler's Law        — complexity is folded, not removed: every action is
//                           still one click away (system owns the grouping).
// Controlled only by the native <details> open state; closes on an outside click
// and right after any action fires.
export default function ZoteroActionsMenu({ actions = [], disabled = false }) {
  const ref = useRef(null);
  useEffect(() => {
    function onDocClick(e) {
      if (ref.current && !ref.current.contains(e.target)) ref.current.open = false;
    }
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, []);
  const run = (fn) => { if (ref.current) ref.current.open = false; fn(); };

  return (
    <details ref={ref} className="relative ml-auto shrink-0">
      <summary className="list-none [&::-webkit-details-marker]:hidden cursor-pointer select-none px-3 py-2 rounded-lg border border-slate-300 text-slate-700 text-sm hover:bg-slate-50 flex items-center gap-1">
        Zotero <span aria-hidden="true" className="text-[10px] text-slate-400">▾</span>
      </summary>
      <div className="absolute right-0 z-20 mt-1 w-72 rounded-lg border border-slate-200 bg-white p-1.5 shadow-lg">
        <p className="px-2 pt-1 pb-1.5 text-[11px] uppercase tracking-wider text-slate-400 font-semibold">
          Write to your library
        </p>
        {actions.map((a) => (
          <button
            key={a.label}
            type="button"
            onClick={() => run(a.onClick)}
            disabled={disabled || a.disabled}
            title={a.title}
            className="w-full text-left px-2 py-1.5 rounded-md text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {a.busy ? a.busyLabel : a.label}
          </button>
        ))}
      </div>
    </details>
  );
}
