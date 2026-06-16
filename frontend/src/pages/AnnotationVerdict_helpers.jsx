// Local helpers for AnnotationVerdict.jsx — extracted to keep the page file
// under 500 LOC. Pure presentational widgets + filter chip definitions used
// only by the annotate-verdict workflow.

import { pretty } from '../utils/priorityLabels.js';

// Filter chips: the `key` stays the wire/enum value the backend filters on; the
// `label` shows the shared human vocabulary (Mental Model / Jakob's Law) so the
// raw `must_read` enum never reaches the user. 'all' and '🎯 border' are not
// priorities — they keep their own labels.
export const PRIORITY_FILTERS = [
  { key: 'must_read', label: pretty('must_read'), cls: 'bg-emerald-600 text-white border-emerald-600' },
  { key: 'should_read', label: pretty('should_read'), cls: 'bg-sky-600 text-white border-sky-600' },
  { key: 'could_read', label: pretty('could_read'), cls: 'bg-amber-500 text-white border-amber-500' },
  { key: 'dont_read', label: pretty('dont_read'), cls: 'bg-rose-600 text-white border-rose-600' },
  { key: '', label: 'all', cls: 'bg-slate-700 text-white border-slate-700' },
  // Sprint-3+ active learning: ranks library rows by border distance.
  // The backend re-trains the regressor each call (~30 s), so the chip
  // shows a loading state on first click; result cached for 5 min.
  { key: 'border', label: '🎯 border', cls: 'bg-violet-600 text-white border-violet-600' },
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
export const FLAG_FILTER_LABELS = Object.fromEntries(
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
  'Annotate = label what you’ve read. Set must / should / could / don’t '
  + '(keys 1–4 · j/k to move between papers) on papers you’ve read. Your verdict '
  + 'becomes ground truth: it retrains the model and is mirrored to Zotero as a note.';

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

// Above the filter chips: one-line summary of the labels the model trains on.
// Renders only when `summary` is loaded (the parent passes null otherwise).
// `total_rows` counts every training label (mostly auto-derived); `yours` is
// the subset carrying a saved verdict — broken down into how many changed the
// model's guess vs. confirmed it. The old copy mislabelled the total as
// "effective labels" and used "overridden/GT" jargon a cold reader can't parse.
export function EffectiveLabelsStrip({ summary }) {
  if (!summary) return null;
  const total = summary.total_rows ?? 0;
  const changed = summary.user_overrode_derivation ?? 0;
  const confirmed = summary.user_confirmed_derivation ?? 0;
  const yours = summary.user_verdicts ?? changed + confirmed;
  const autoDerived = Math.max(total - yours, 0);
  const coverage = total > 0 ? Math.round((yours / total) * 100) : 0;
  // Provisional "Add to library" verdicts are counted SEPARATELY from yours
  // (June 2026: they used to inflate user_verdicts). `outcome_corrected` is the
  // subset already superseded by the observed 7-day shelf outcome.
  const provisional = summary.machine_provisional ?? 0;
  const corrected = summary.outcome_corrected ?? 0;
  return (
    <div
      className="mb-2 px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200 text-[12px] text-slate-700"
      role="status"
      aria-label="Training-label summary"
      title={
        `${total.toLocaleString()} labels train the model. `
        + `${yours.toLocaleString()} (${coverage}%) carry your explicit verdict `
        + `(${changed} changed the model's guess, ${confirmed} confirmed it); `
        + `the other ${autoDerived.toLocaleString()} are auto-derived.`
        + (provisional || corrected
          ? ` Plus ${(provisional + corrected).toLocaleString()} provisional "Add to library" labels`
            + ` — not deliberate verdicts: ${corrected} already corrected by what you actually`
            + ' did with the paper in Zotero (kept/trashed within 7 days), the rest pending.'
          : '')
      }
    >
      <span className="font-semibold text-slate-900">{total.toLocaleString()}</span>{' '}
      training labels ·{' '}
      <span className="text-emerald-800 font-semibold">{yours.toLocaleString()}</span>{' '}
      yours <span className="text-emerald-800 font-semibold">({coverage}%)</span>{' '}
      <span className="text-slate-500">
        ({changed} changed · {confirmed} confirmed)
      </span>
      {(provisional > 0 || corrected > 0) && (
        <span className="text-slate-500">
          {' '}· {provisional + corrected} adds{corrected > 0 ? ` (${corrected} outcome-corrected)` : ''}
        </span>
      )}
    </div>
  );
}

// Goal-Gradient + Zeigarnik: a truthful, visible completion signal for the
// pile the user is working through. `labeled` = visible rows that already carry
// the user's verdict; the bar fills toward the finish line as triage proceeds,
// and the loop is closed explicitly at 100% ("All N labeled ✓"). Distinct from
// the "Showing X of Y" list-size readout, which is filter feedback, not progress.
export function TriageProgress({ labeled, total }) {
  if (!total) return null;
  const pct = Math.min(Math.round((labeled / total) * 100), 100);
  const done = labeled >= total;
  return (
    <div className="mb-2" role="status" aria-label={`${labeled} of ${total} labeled`}>
      <div className="flex items-center justify-between text-[11px] mb-1">
        <span className="text-slate-600">
          {done ? `All ${total} labeled` : `${labeled} of ${total} labeled`}
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
