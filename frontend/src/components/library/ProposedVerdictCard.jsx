import { useState } from 'react';
import { submitVerdict } from '../../api/goldenApi.js';
import { queueRejectTag } from '../../api/libraryApi.js';
import { pretty } from '../../utils/priorityLabels.js';
import { GRADE_CLS } from './shared.jsx';

// The Confirm/Override card (Phase 2): the review-fleet has PRE-DECIDED a reading
// verdict for this paper (rec.proposed_verdict, computed from cached deep-review
// signals — never an LLM call here). The human only ratifies it, so the surface
// has exactly TWO primary actions (Tesler's Law: the system owns the decision;
// Hick's Law: not the four-button picker on every row):
//
//   Confirm  → one-tap submitVerdict(proposed); dont_read also queues the ❌ tag
//              (same reject path as InlineAnnotate). Then onSaved() collapses +
//              refetches, so the ratified paper drops out of the queue.
//   Override → expand the row so the EXISTING InlineAnnotate → VerdictPanel shows,
//              with the proposal pre-selected as derivedPriority. No new editor.
//
// AMBIGUITY GOES TO THE HUMAN (Tesler's Law): when the proposal is low-confidence
// OR carries any quality flag, the one-tap Confirm is WITHHELD — only Override is
// offered, forcing the human to look. A `dont_read` proposal is shown but its
// Confirm is the reject path, made visually distinct (rose) from a keep.
//
// INDIRECT-PROMPT-INJECTION: the proposal is ingested from PDF/abstract-derived
// signals, so it NEVER auto-writes. Every write here is behind an explicit click.

// Below this the proposal is treated as uncertain → Confirm is withheld. A clean
// `read`+A/B scores 0.85, a plain `read` 0.65, a goal-miss skip 0.75; the shaky
// cases (no digest 0.35, ungraded skim 0.45, any uncertain/overstatement −0.2)
// fall under it. Matches services/library/review_fleet/propose.py::_confidence.
const CONFIDENCE_FLOOR = 0.6;

// Short, human-readable gloss for the propose.py flag codes (kept terse —
// Cognitive Load: a chip, not a sentence). Unknown codes fall through verbatim.
const FLAG_LABELS = {
  quality_uncertain: 'quality uncertain',
  overstatements: 'overstated claims',
  quality_flag: 'quality concern',
  red_flags: 'red flags',
};

// Distinct from the amber 🏷 user-label chip and the teal ★ score (Von Restorff):
// a keep proposal reads indigo (the Digest family), a Remove proposal reads rose
// so the one destructive ratification is unmistakable.
const PROPOSAL_CLS = {
  keep: 'bg-indigo-100 text-indigo-900 border-indigo-300',
  remove: 'bg-rose-100 text-rose-800 border-rose-300',
};

export default function ProposedVerdictCard({ itemKey, proposal, onSaved, onOverride }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  if (!proposal || !proposal.proposed) return null;

  const proposed = proposal.proposed;
  const isRemove = proposed === 'dont_read';
  const flags = (proposal.flags || []).filter(Boolean);
  const confidence = typeof proposal.confidence === 'number' ? proposal.confidence : 0;
  // Tesler's Law: ambiguity (low confidence OR any flag) is handed back to the
  // human — the one-tap Confirm is withheld, leaving only Override.
  const confirmable = confidence >= CONFIDENCE_FLOOR && flags.length === 0;

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      // Same path as InlineAnnotate's submit: a dont_read also queues the ❌ tag,
      // so there is ONE reject path (Occam's Razor), behind this explicit click.
      const tasks = [submitVerdict({ item_key: itemKey, user_priority: proposed })];
      if (isRemove) tasks.push(queueRejectTag(itemKey));
      await Promise.all(tasks);
      onSaved?.();
    } catch (e) {
      setError(`Couldn’t save: ${e.message || e}`);
      setSubmitting(false);
    }
  }

  const chipCls = isRemove ? PROPOSAL_CLS.remove : PROPOSAL_CLS.keep;
  return (
    <div className="mt-2 rounded-xl border border-indigo-200 bg-indigo-50/40 p-2.5 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider font-semibold text-indigo-500">
          Proposed
        </span>
        {/* Von Restorff: the proposal chip — its own color, distinct from 🏷 amber
            label and ★ teal score, so the pre-decision reads as its own thing. */}
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold border ${chipCls}`}
          title="The review fleet pre-decided this reading verdict from the paper's cached deep-review signals. Confirm to accept, or Override to change it."
        >
          {pretty(proposed)}
        </span>
        {proposal.grade && (
          <span
            className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${GRADE_CLS[proposal.grade] || 'bg-slate-100 text-slate-700 border-slate-300'}`}
            title="Full-text quality grade the proposal drew on"
          >
            Quality {proposal.grade}
          </span>
        )}
        <span
          className="text-[10px] text-indigo-500"
          title="How confident the fleet is in this proposal (from the agreement + strength of the cached signals)"
        >
          {Math.round(confidence * 100)}% confident
        </span>
      </div>

      {proposal.rationale && (
        <p className="text-[11px] text-slate-700">{proposal.rationale}</p>
      )}

      {flags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {flags.map((f) => (
            <span
              key={f}
              className="px-1.5 py-0 rounded-full text-[10px] font-medium bg-amber-100 text-amber-900 border border-amber-300"
              title="A quality signal worth a human check — so the one-tap Confirm is withheld here."
            >
              ⚠ {FLAG_LABELS[f] || f}
            </span>
          ))}
        </div>
      )}

      {!confirmable && (
        <p className="text-[10px] text-amber-700">
          {flags.length > 0
            ? 'Flagged for a check — review it before deciding.'
            : 'Low confidence — review it before deciding.'}
        </p>
      )}

      {error && <p className="text-[11px] text-rose-700">{error}</p>}

      {/* Exactly TWO primary actions (Hick's/Tesler's) — and only ONE (Override)
          when the proposal is ambiguous, so an uncertain call always goes to the
          human rather than a one-tap accept. */}
      <div className="flex items-center gap-2">
        {confirmable && (
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className={`px-3 py-1 rounded-lg text-xs font-semibold text-white disabled:opacity-50 ${
              isRemove ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700'
            }`}
            title={isRemove
              ? 'Accept the proposal: remove this paper (queues the ❌ tag) and record the label.'
              : 'Accept the proposal: record this reading priority and pin/handle the paper.'}
          >
            {submitting ? 'Saving…' : `Confirm — ${pretty(proposed)}`}
          </button>
        )}
        <button
          type="button"
          onClick={onOverride}
          disabled={submitting}
          className="px-3 py-1 rounded-lg text-xs font-semibold border border-slate-300 text-slate-700 bg-white hover:bg-slate-50 disabled:opacity-50"
          title="Open the full review to pick a different verdict (the proposal is pre-selected)."
        >
          Override
        </button>
      </div>
    </div>
  );
}
