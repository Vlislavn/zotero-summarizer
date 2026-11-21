// Local helpers for AnnotationVerdict.jsx — extracted to keep the page file
// under 500 LOC. Pure presentational widgets + filter chip definitions used
// only by the annotate-verdict workflow.

import { pretty } from '../utils/priorityLabels.js';
import { DECISIONS, DECISION_TINT } from '../components/decisionVocab.js';

// Filter chips: the `key` stays the wire/enum value the backend filters on; the
// pills are the shared decision vocabulary (components/decisionVocab.js — same
// words and tints as Today and the verdict panel) so the raw `must_read` enum
// never reaches the user. 'all' and '🎯 border' are not priorities — they keep
// their own labels.
export const PRIORITY_FILTERS = [
  { key: '', label: 'All', cls: 'bg-slate-700 text-white border-slate-700' },
  ...DECISIONS.map(([key, label]) => ({ key, label, cls: DECISION_TINT[key] })),
  // NOTE: 'border' (active-learning) is intentionally NOT a chip here — it swaps
  // the data source and polls for minutes while it re-scores the library, so it
  // renders as a distinct labelled button in AnnotationVerdict, not a cheap
  // instant-filter chip (Jakob's Law / Hick's Law).
];

// Diagnostic flag categories (the active-learning audit lens) — NOT the reading
// priority enum, so they don't go through `pretty()`. They still get human copy so
// the raw `must_read`/`near_must_read` wire codes never reach the user (same
// Mental-Model rule the PRIORITY_FILTERS above follow). `key` stays the wire value
// the backend filters on; `label` is what the chip shows.
export const FLAG_FILTERS = [
  { key: '', label: 'any' },
  { key: 'weak_must_read', label: 'weak top pick' },
  { key: 'near_must_read', label: 'near top pick' },
  { key: 'manual_override', label: 'manual override' },
];

// key -> human label for the flag chips (mirrors FLAG_FILTERS), used to render the
// ACTIVE flag chip without leaking the raw wire code. Unknown keys fall through.
const FLAG_FILTER_LABELS = Object.fromEntries(
  FLAG_FILTERS.filter((f) => f.key).map((f) => [f.key, f.label]),
);
export const prettyFlag = (key) => FLAG_FILTER_LABELS[key] || key;

// Batch-mode keyboard map (Jakob's Law: Gmail/Vim conventions for j/k; inbox-triage 1-4).
export const PRIORITY_BY_KEY = {
  1: 'must_read',
  2: 'should_read',
  3: 'could_read',
  4: 'dont_read',
};

// Orientation-banner copy — Annotate's equivalent of Today's hint. The shared
// <HintBanner storageKey=… > (components/ui/HintBanner.jsx) owns the dismissible
// teal card + localStorage logic; these constants supply the key + text so the
// page renders it with the same copy and dismiss key it always used. It names
// the full consequence of a label so the retrain→Zotero loop lives in the copy,
// not the user's head (Tesler's Law).
export const ANNOTATE_HINT_KEY = 'annotate_hint_dismissed_v1';
export const ANNOTATE_HINT_TEXT =
  'My verdicts: give papers you’ve read your own verdict — must / should / could / not interested '
  + '(keys 1–4 · j/k to move between papers · z to undo). It starts on one verdict group at a time; '
  + 'pick another chip to see the rest. Your verdict is saved as a label and mirrored to Zotero as a note; retraining is manual, in Ops.';

// Active-learning order for the border (uncertain-picks) list: surface the most
// decision-worthy papers FIRST — the ones where the model's current label and its
// prediction DISAGREE (a 'conflict' flag, the highest-value labels), then the rest
// closest to the decision boundary (smallest `border_distance` = least certain).
// Stable, so equal-rank items keep the backend's order. Pure → unit-testable
// without the network. The backend returns these unordered; labeling the most
// uncertain first is what makes active learning pay off per click.
export function sortBorderByUncertainty(items) {
  const rank = (it) => {
    const conflict = Array.isArray(it.flags) && it.flags.includes('conflict') ? 0 : 1;
    const dist = typeof it.border_distance === 'number' ? it.border_distance : Number.POSITIVE_INFINITY;
    return [conflict, dist];
  };
  return [...(items || [])].sort((a, b) => {
    const [ca, da] = rank(a);
    const [cb, db] = rank(b);
    return ca - cb || da - db;
  });
}

export function FilterChip({ active, onClick, children, activeCls }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
        active
          ? `${activeCls || 'bg-slate-900 text-white border-slate-900'} ring-2 ring-offset-1 ring-slate-400 before:content-['✓'] before:mr-1`
          : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-100'
      }`}
    >
      {children}
    </button>
  );
}

// ErrorBanner now lives in components/library/shared.jsx (single source, runs
// values through humanizeError); re-exported so AnnotationVerdict.jsx's import
// keeps resolving.
export { ErrorBanner } from '../components/library/shared.jsx';

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
        Model’s guess:{' '}
        <span className="font-semibold text-slate-900">{pretty(derived)}</span> · this
        is what the model uses today. Pick a verdict to replace it.
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
        Your verdict (<span className="font-semibold">{pretty(userPriority)}</span>)
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
      Your verdict (<span className="font-bold">{pretty(userPriority)}</span>) overrides
      the model’s guess
      {derived && (
        <>
          {' '}
          (<span className="font-semibold">{pretty(derived)}</span>)
        </>
      )}
      . Used as ground truth for retraining.
    </div>
  );
}

// Goal-Gradient + Zeigarnik: a truthful, visible completion signal for the
// pile the user is working through. `labeled` = visible rows that already carry
// the user's verdict; the bar fills toward the finish line as triage proceeds,
// and the loop is closed explicitly at 100% ("All N labeled ✓"). Distinct from
// the "Showing X of Y" list-size readout, which is filter feedback, not progress.
export function TriageProgress({ labeled, total, scope = '' }) {
  if (!total) return null;
  const pct = Math.min(Math.round((labeled / total) * 100), 100);
  const done = labeled >= total;
  return (
    <div className="mb-2" role="status" aria-label={`${scope ? `${scope}: ` : ''}${labeled} of ${total} labeled by you`}>
      <div className="flex items-center justify-between text-[11px] mb-1">
        <span className="text-slate-600">
          {scope && <strong>{scope}: </strong>}
          {done ? `All ${total} labeled by you` : `${labeled} of ${total} labeled by you`}
        </span>
        <span className={done ? 'text-emerald-700 font-semibold' : 'text-slate-400'}>
          {done ? '✓' : `${total - labeled} to go`}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-slate-200 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${done ? 'bg-emerald-500' : 'bg-teal-500'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
