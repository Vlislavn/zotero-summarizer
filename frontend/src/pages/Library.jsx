import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import LibraryReadNext from './LibraryReadNext.jsx';
import AnnotationVerdict from './AnnotationVerdict.jsx';

// Library page — one surface, two modes (Increment 3 nav collapse). The default
// "Read next" mode is the unchanged ranked reading queue (LibraryReadNext); the
// "Batch label" mode is the former Annotate page (AnnotationVerdict), which owns
// the shared VerdictPicker + the 1/2/3/4 priority keys + j/k navigation flow.
// Folding it in here drops a top-level nav entry (Hick's Law) while keeping the
// whole keyboard-driven labelling workflow reachable — and the deep-link the
// "Read next" queue uses (?item_key=…) still opens batch mode on the right paper.
//
// Mode lives in ?mode=batch so the old /annotate bookmark redirects cleanly to
// /library?mode=batch (App.jsx). Read-next is the default (no param) — the daily
// landing surface stays first.

const MODES = [
  { id: 'read', label: 'Read next' },
  { id: 'batch', label: 'Batch label' },
];

const VALID_MODES = new Set(MODES.map((m) => m.id));
const DEFAULT_MODE = 'read';

function readMode(searchParams) {
  const m = searchParams.get('mode');
  return m && VALID_MODES.has(m) ? m : DEFAULT_MODE;
}

export default function Library() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [mode, setMode] = useState(() => readMode(searchParams));

  // Track URL-driven mode changes (e.g. the /annotate redirect lands on
  // ?mode=batch, or a Read-next "open" deep-link carries ?item_key=…).
  useEffect(() => {
    setMode(readMode(searchParams));
  }, [searchParams]);

  function selectMode(id) {
    setMode(id);
    const next = new URLSearchParams(searchParams);
    if (id === DEFAULT_MODE) next.delete('mode');
    else next.set('mode', id);
    const qs = next.toString();
    navigate({ search: qs ? `?${qs}` : '' }, { replace: true });
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Library modes"
        className="flex flex-wrap gap-1.5 mb-4"
      >
        {MODES.map((m) => {
          const active = m.id === mode;
          return (
            <button
              key={m.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectMode(m.id)}
              className={[
                'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors border',
                active
                  ? 'bg-slate-900 text-white border-slate-900'
                  : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-100',
              ].join(' ')}
            >
              {m.label}
            </button>
          );
        })}
      </div>
      {mode === 'batch' ? <AnnotationVerdict /> : <LibraryReadNext />}
    </div>
  );
}
