// Local helpers for AnnotationVerdict.jsx — extracted to keep the page file
// under 500 LOC. Pure presentational widgets + filter chip definitions used
// only by the annotate-verdict workflow.

import { useState } from 'react';

export const PRIORITY_FILTERS = [
  { key: 'must_read', label: 'must_read', cls: 'bg-emerald-600 text-white border-emerald-600' },
  { key: 'should_read', label: 'should_read', cls: 'bg-sky-600 text-white border-sky-600' },
  { key: 'could_read', label: 'could_read', cls: 'bg-amber-500 text-white border-amber-500' },
  { key: 'dont_read', label: 'dont_read', cls: 'bg-rose-600 text-white border-rose-600' },
  { key: '', label: 'all', cls: 'bg-slate-700 text-white border-slate-700' },
  // Sprint-3+ active learning: ranks library rows by border distance.
  // The backend re-trains the regressor each call (~30 s), so the chip
  // shows a loading state on first click; result cached for 5 min.
  { key: 'border', label: '🎯 border', cls: 'bg-violet-600 text-white border-violet-600' },
];

export const FLAG_FILTERS = [
  { key: '', label: 'any' },
  { key: 'weak_must_read', label: 'weak_must_read' },
  { key: 'near_must_read', label: 'near_must_read' },
  { key: 'manual_override', label: 'manual_override' },
];

// Batch-mode keyboard map (Jakob's Law: Gmail/Vim conventions for j/k; inbox-triage 1-4).
export const PRIORITY_BY_KEY = {
  1: 'must_read',
  2: 'should_read',
  3: 'could_read',
  4: 'dont_read',
};

export function FilterChip({ active, onClick, children, activeCls }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
        active
          ? activeCls || 'bg-slate-900 text-white border-slate-900'
          : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-100'
      }`}
    >
      {children}
    </button>
  );
}

export function ErrorBanner({ error, title = 'Error' }) {
  if (!error) return null;
  return (
    <div className="my-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
      <span className="font-semibold">{title}:</span> {error.message || String(error)}
    </div>
  );
}

export function AbstractBlock({ abstract }) {
  const [expanded, setExpanded] = useState(false);
  if (!abstract) return null;
  return (
    <div>
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-1">
        Abstract
      </h3>
      <div
        className={`text-sm text-slate-700 whitespace-pre-line ${
          expanded ? '' : 'line-clamp-5'
        }`}
      >
        {abstract}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-1 text-[11px] text-teal-700 hover:text-teal-900 font-medium"
      >
        {expanded ? 'Show less' : 'Show more'}
      </button>
    </div>
  );
}

// Above the verdict buttons: tells the user whether their verdict is being
// used as ground truth for retraining (Phase 1.18 "Used as GT" surfacing).
// Pure presentational — reads only from `detail.provenance.derived_priority`
// and `detail.verdict.user_priority`, both already in the review-detail payload.
export function GroundTruthOneLiner({ detail }) {
  if (!detail) return null;
  const derived = detail.provenance?.derived_priority ?? null;
  const userPriority = detail.verdict?.user_priority ?? null;

  // Case 1: no user verdict yet — slate informational copy.
  if (!userPriority) {
    if (!derived) return null;
    return (
      <div
        className="px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200 text-[12px] text-slate-700"
        role="note"
      >
        Derived label:{' '}
        <span className="font-semibold text-slate-900">{derived}</span> · this
        is what the model uses today. Change it to overlay.
      </div>
    );
  }

  // Case 2: user verdict matches the derivation — confirmed.
  if (derived && userPriority === derived) {
    return (
      <div
        className="px-3 py-1.5 rounded-lg bg-emerald-50 border border-emerald-200 text-[12px] text-emerald-900"
        role="note"
      >
        <span aria-hidden="true">★ </span>
        Your verdict (<span className="font-semibold">{userPriority}</span>)
        is used as ground truth for retraining.
      </div>
    );
  }

  // Case 3: user verdict overrides the derivation — emphasized green.
  return (
    <div
      className="px-3 py-1.5 rounded-lg bg-emerald-100 border border-emerald-300 text-[12px] text-emerald-900 font-medium"
      role="note"
    >
      <span aria-hidden="true">★ </span>
      Your verdict (<span className="font-bold">{userPriority}</span>) overrides
      the derived label
      {derived && (
        <>
          {' '}
          (<span className="font-semibold">{derived}</span>)
        </>
      )}
      . Used as ground truth for retraining.
    </div>
  );
}

// Above the filter chips: one-line summary of the hybrid GT pipeline counts.
// Renders only when `summary` is loaded (the parent passes null otherwise).
export function EffectiveLabelsStrip({ summary }) {
  if (!summary) return null;
  const total = summary.total_rows ?? 0;
  const overridden = summary.user_overrode_derivation ?? 0;
  const confirmed = summary.user_confirmed_derivation ?? 0;
  return (
    <div
      className="mb-2 px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200 text-[12px] text-slate-700"
      role="status"
      aria-label="Effective labels summary"
    >
      <span className="font-semibold text-slate-900">Effective labels:</span>{' '}
      {total.toLocaleString()} total ·{' '}
      <span className="text-emerald-800 font-semibold">{overridden}</span>{' '}
      user-overridden ·{' '}
      <span className="text-emerald-800 font-semibold">{confirmed}</span>{' '}
      user-confirmed
    </div>
  );
}

export function PdfButton({ pdfPath, hasPdf }) {
  const [copied, setCopied] = useState(false);
  if (!hasPdf || !pdfPath) return null;
  async function handleClick() {
    try {
      await navigator.clipboard.writeText(pdfPath);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      // Fallback: prompt the user with the path so they can copy manually.
      window.prompt('Copy this PDF path:', pdfPath);
    }
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-700"
      title={pdfPath}
    >
      {copied ? 'PDF path copied to clipboard ✓' : 'Copy PDF path'}
    </button>
  );
}
