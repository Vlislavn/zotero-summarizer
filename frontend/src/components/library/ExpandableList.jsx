import { useState } from 'react';

// A long list in a capped box with a visible way to see the rest: a fade at the
// bottom edge says "more below" while collapsed, and "Show all N" lifts the cap
// (L-07 — the clipped last row read as a rendering bug, not a scroll box).
export default function ExpandableList({ count, children, limit = 8 }) {
  const [open, setOpen] = useState(false);
  const long = count > limit;
  return (
    <div>
      <div className={`${open ? '' : 'max-h-56 overflow-auto'} pr-1 slim-scroll`}>
        {children}
        {long && !open && (
          <div aria-hidden="true" className="pointer-events-none sticky bottom-0 -mt-6 h-6 bg-gradient-to-t from-slate-50 to-transparent" />
        )}
      </div>
      {long && (
        <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
          className="mt-1 min-h-8 text-xs text-forest-800 underline underline-offset-2">
          {open ? 'Show fewer' : `Show all ${count}`}
        </button>
      )}
    </div>
  );
}
