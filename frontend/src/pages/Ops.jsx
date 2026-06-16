import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import Review from './Review.jsx';
import Triage from './Triage.jsx';
import Pending from './Pending.jsx';

// Ops surface — the former Feed Review / Triage Monitor / Pending Changes power
// tools folded into ONE page with three tabs (Hick's/Miller's Law: three rarely-
// used operator screens collapse to a single nav entry + a 3-choice tab strip,
// not three top-level links). Each tab renders the existing, unmodified page body
// (Review.jsx / Triage.jsx / Pending.jsx) so behavior is identical — those
// components still own their own data + deep-link params (e.g. Review reads
// ?state=gate_rejected from the URL).
//
// Tab selection comes from EITHER ?tab=<id> or the URL hash (#review etc.) so
// the old per-page bookmarks keep working after App.jsx redirects:
//   /review  -> /ops?tab=review   (+ any ?state passthrough)
//   /triage  -> /ops?tab=triage
//   /pending -> /ops?tab=pending

const TABS = [
  { id: 'review', label: 'Feed review', Body: Review },
  { id: 'triage', label: 'Triage jobs', Body: Triage },
  { id: 'pending', label: 'Pending changes', Body: Pending },
];

const VALID_TABS = new Set(TABS.map((t) => t.id));
const DEFAULT_TAB = 'review';

// Read the desired tab from ?tab= first, then the #hash, defaulting to review.
function readInitialTab(searchParams, hash) {
  const fromQuery = searchParams.get('tab');
  if (fromQuery && VALID_TABS.has(fromQuery)) return fromQuery;
  const fromHash = (hash || '').replace(/^#/, '');
  if (fromHash && VALID_TABS.has(fromHash)) return fromHash;
  return DEFAULT_TAB;
}

export default function Ops() {
  const [searchParams] = useSearchParams();
  const { hash } = useLocation();
  const navigate = useNavigate();
  const [tab, setTab] = useState(() => readInitialTab(searchParams, hash));

  // Keep the active tab in sync when the URL changes underneath us (e.g. a
  // redirect from /review lands on /ops?tab=review, or a deep link with a hash).
  useEffect(() => {
    setTab(readInitialTab(searchParams, hash));
  }, [searchParams, hash]);

  // On click, write ?tab= into the URL (replace — tab switches aren't history
  // steps) while preserving any other params a body reads (e.g. ?state=). Drop a
  // stale #hash so the canonical form is the query param.
  function selectTab(id) {
    setTab(id);
    const next = new URLSearchParams(searchParams);
    next.set('tab', id);
    navigate({ search: `?${next.toString()}` }, { replace: true });
  }

  const ActiveBody = (TABS.find((t) => t.id === tab) ?? TABS[0]).Body;

  return (
    <div>
      <div
        role="tablist"
        aria-label="Ops sections"
        className="flex flex-wrap gap-1.5 mb-4"
      >
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectTab(t.id)}
              className={[
                'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors border',
                active
                  ? 'bg-slate-900 text-white border-slate-900'
                  : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-100',
              ].join(' ')}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <ActiveBody />
    </div>
  );
}
