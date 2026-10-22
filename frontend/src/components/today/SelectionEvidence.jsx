import { Chip, Disclosure } from '../paper/review/primitives.jsx';
import { gradeTone } from '../paper/review/tones.js';

const POLICY = { control: 'Relevance-led (control)', quality_first: 'Quality-first' };
const RANK_BASIS = {
  relevance_goal_citation: 'Relevance, goals and known citations',
  quality_first: 'Quality-led cohort key',
};
const PICKER_BASIS = {
  model_adjusted: 'Model-role key with the recorded quality adjustment',
  cohort: 'Cohort key', surprise: 'Surprise key',
};
const ROLE_REASON = {
  model: 'Main recommendation from the eligible pool.',
  surprise: 'Exploration: an unexpected pick, not a stronger quality endorsement.',
  diversity: 'Exploration: outside your usual saved-paper mix, not a stronger quality endorsement.',
};

function reviewPresentation(paper) {
  const review = paper.selection_evidence?.review;
  const grade = ['A', 'B', 'C', 'D'].includes(paper.quality?.grade) ? paper.quality.grade : null;
  if (review?.join === 'matched') {
    if (review.needs_pdf === true) return {
      text: 'Review: full text unavailable', tone: 'amber',
      detail: 'The current entry needs readable full text. Access failure is not a scientific-quality judgment.',
    };
    if (review.digest_usable === true) return {
      text: grade && review.quality_origin === 'current_entry'
        ? `Evidence: grade ${grade} (AI review)` : 'Review available · evidence grade unknown',
      tone: review.quality_origin === 'current_entry' ? gradeTone(grade) : 'slate',
      detail: 'A current usable review matched. Its AI assessment is not independent scientific validation.',
    };
    if (review.digest_usable === false) return {
      text: 'Review: not ready', tone: 'slate',
      detail: 'A current entry matched, but its digest is not usable. Failure and staleness are not recorded here.',
    };
  }
  if (review?.quality_origin === 'legacy_row' && grade) return {
    text: `Evidence: legacy grade ${grade} · current review unconfirmed`, tone: 'slate',
    detail: 'This grade comes from the saved triage row. A current join miss does not establish whether a review is absent, stale or failed.',
  };
  if (paper.review_ready === true && review?.join !== 'unavailable') return {
    text: 'Review available · evidence origin unrecorded', tone: 'slate',
    detail: 'This older payload reports an available review but does not record its quality origin.',
  };
  return {
    text: 'Review evidence: unknown', tone: 'slate',
    detail: 'No usable current review is established here. A join miss does not establish whether a review is absent, stale or failed; missing evidence is not a neutral or negative grade.',
  };
}

function roleReason(paper) {
  const selection = paper.selection_evidence?.selection;
  if (!PICKER_BASIS[selection?.basis]) return 'Selection details were not recorded for this card.';
  const requested = selection.requested_role;
  if (paper.role === 'model_fallback' && ['surprise', 'diversity'].includes(requested)) {
    return `Main recommendation filling an unfilled ${requested} slot.`;
  }
  return ROLE_REASON[requested] || 'Selection role was not recorded.';
}

function observedNumber(value) {
  return Number.isFinite(value) ? String(value) : 'Unknown';
}

function Observations({ paper, selectionPolicy }) {
  const evidence = paper.selection_evidence;
  if (!evidence || evidence.basis === 'not_observed') return null;
  const selection = evidence.selection;
  const stored = evidence.stored;
  const priority = ['must_read', 'should_read', 'could_read', 'dont_read'].includes(stored?.reading_priority)
    ? stored.reading_priority : 'Unknown';
  const score = stored?.composite_recorded === false ? 'Composite not recorded'
    : Number.isFinite(paper.composite_score) ? `${paper.composite_score.toFixed(2)}/5` : 'Unknown';
  const rows = [
    ['Active policy', POLICY[selectionPolicy] || 'Not recorded'],
    ['Displayed composite', score],
    ['Stored priority (separate)', priority],
    ['Cohort ranking', RANK_BASIS[evidence.rank?.basis] || 'Not recorded'],
    ['Cohort key', observedNumber(evidence.rank?.key)],
    ['Picker', PICKER_BASIS[selection?.basis] || 'Not recorded'],
    ['Picker key', observedNumber(selection?.key)],
    ['Quality adjustment', selection?.bonus_policy === 'not_used' ? 'Not used'
      : observedNumber(selection?.quality_bonus)],
  ];
  return <>
    <dl className="mt-2 space-y-1">
      {rows.map(([label, value]) => <div key={label} className="flex flex-wrap gap-x-2">
        <dt className="text-slate-600">{label}:</dt><dd className="break-words">{value}</dd>
      </div>)}
    </dl>
    {selection?.band_restricted === true && <p className="mt-2">
      The quality adjustment reorders only within the existing relevance-category slots.
    </p>}
    <p className="mt-2">These are selection observations, not raw model predictions or calibrated probabilities.
      {' '}Stored priority is separate from the displayed composite band. Cards keep the server&apos;s role-grouped order, not composite-score order.</p>
  </>;
}

export default function SelectionEvidence({ paper, selectionPolicy }) {
  const evidence = paper.selection_evidence;
  const blinded = selectionPolicy === 'interleave' || evidence?.mode === 'interleave'
    || evidence?.basis === 'unavailable_blinded';
  const review = blinded ? { text: 'Review evidence: unavailable (blinded selection)', tone: 'slate' }
    : reviewPresentation(paper);
  return <div className="mt-2 space-y-2 [&_summary]:text-slate-600">
    <Chip tone={review.tone} className="max-w-full">{review.text}</Chip>
    <Disclosure summary="Why selected">
      <div className="text-xs text-slate-600 leading-relaxed break-words">
        {blinded ? <p>Review and selection details are withheld to keep this selection blinded.</p> : <>
          <p>{roleReason(paper)}</p>
          {Array.isArray(paper.why) && paper.why.length > 0 && <ul className="mt-1 list-disc pl-4">
            {paper.why.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>}
          <p className="mt-2">{review.detail}</p>
          <Observations paper={paper} selectionPolicy={selectionPolicy} />
        </>}
      </div>
    </Disclosure>
  </div>;
}
