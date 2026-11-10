import { Disclosure } from '../paper/review/primitives.jsx';
import { CHIP_TONE, gradeTone, qualityBasis } from '../paper/review/tones.js';

const ANALYZED = 'analyzed-today-v1';
const POLICY = {
  control: 'Relevance-led (control)', quality_first: 'Quality-first',
  [ANALYZED]: 'Analyzed (usable full-text review required)',
};
const RANK_BASIS = {
  relevance_goal_citation: 'Relevance, goals and known citations',
  quality_first: 'Quality-led cohort key',
  goal_cos: 'Similarity to your goal text',
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

// Plain one-line answer to "why is this here?" (English, lower-case clauses). Honest
// details live behind “more” in the disclosure below.
const WHY_WORD = {
  'Strong goal match': 'very close to your goals', 'On-topic for you': 'on topic for your goals',
  "Like papers you've saved": 'like papers you saved', 'Off your usual track': 'off your usual track',
  'High model relevance': 'high model score', 'Highly cited': 'highly cited', Surprising: 'surprising',
};
const ROLE_LINE = {
  surprise: 'Surprise pick', diversity: 'Outside your usual mix', model: 'Top recommendation',
};
const GOAL_WHY = new Set(['Strong goal match', 'On-topic for you']);

function whyWord(reason) {
  const senior = /^Senior authors \(h=(\d+)\)$/.exec(reason);
  return senior ? `senior authors (h-index ${senior[1]})` : WHY_WORD[reason];
}

function selectionLine(paper, selectionPolicy, blinded) {
  if (blinded) return 'Reasons hidden to keep the selection blind.';
  // goals.yaml entries may carry stray quotes/commas; display-trim only.
  const goal = paper.selection_evidence?.rank?.goal?.replace(/^[\s"'“”«»]+|[\s"'“”«»,;]+$/g, '');
  if (selectionPolicy === ANALYZED) {
    return goal ? `Close to your goal “${goal}”` : 'Selected from your reviewed papers';
  }
  const selection = paper.selection_evidence?.selection;
  if (!PICKER_BASIS[selection?.basis]) return 'Selection reasons not recorded';
  const goalLed = paper.selection_evidence?.rank?.basis === 'goal_cos'
    && !['surprise', 'diversity'].includes(selection.requested_role);
  const goalHead = goal ? `Close to your goal “${goal}”` : 'Close to your goals';
  const head = goalLed ? goalHead : ROLE_LINE[selection.requested_role] || 'Picked for you';
  const tail = (Array.isArray(paper.why) ? paper.why : [])
    .filter((reason) => !(goalLed && GOAL_WHY.has(reason))).map(whyWord).filter(Boolean).slice(0, 2);
  return [head, ...tail].join(' · ');
}

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
        ? `Quality ${[grade, qualityBasis(paper.quality)].filter(Boolean).join(' · ')}` : null,
      tone: review.quality_origin === 'current_entry' ? gradeTone(grade) : 'slate',
      detail: 'A current usable review matched. Its AI assessment is not independent scientific validation.',
    };
    if (review.digest_usable === false) return {
      text: 'Review: not ready', tone: 'slate',
      detail: 'A current entry matched, but its digest is not usable. Failure and staleness are not recorded here.',
    };
  }
  if (review?.quality_origin === 'legacy_row' && grade) return {
    text: `Legacy quality ${[grade, qualityBasis(paper.quality)].filter(Boolean).join(' · ')} · current review unconfirmed`, tone: 'slate',
    detail: 'This grade comes from the saved triage row. A current join miss does not establish whether a review is absent, stale or failed.',
  };
  if (paper.review_ready === true && review?.join !== 'unavailable') return {
    text: null, tone: 'slate',
    detail: 'This older payload reports an available review but does not record its quality origin.',
  };
  return {
    text: null, tone: 'slate',
    detail: 'No usable current review is established here. A join miss does not establish whether a review is absent, stale or failed; missing evidence is not a neutral or negative grade.',
  };
}

function roleReason(paper, selectionPolicy) {
  if (selectionPolicy === ANALYZED) {
    return paper.role === 'exploration'
      ? 'Random check from just below the bar; flagged quality.'
      : 'Has a usable full-text review and passes the quality screen. Ordered by reading priority, then goal similarity.';
  }
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
    ['Policy at selection', POLICY[selectionPolicy] || 'Not recorded'],
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
      {' '}Stored priority is separate from the displayed composite band.
      {selectionPolicy !== ANALYZED && ' Cards keep the server’s role-grouped order, not composite-score order.'}</p>
  </>;
}

function evidenceView({ paper, selectionPolicy, currentStatus, statusFresh, retained = false }) {
  const evidence = paper.selection_evidence;
  const blinded = selectionPolicy === 'interleave' || evidence?.mode === 'interleave'
    || evidence?.basis === 'unavailable_blinded';
  const observed = retained ? { ...paper, quality: currentStatus?.quality, review_ready: currentStatus?.review_ready,
    selection_evidence: { review: currentStatus?.review_evidence } } : paper;
  const review = blinded ? { text: 'Review evidence: unavailable (blinded selection)', tone: 'slate' }
    : retained && (!statusFresh || !currentStatus?.available)
      ? { text: 'Review status: not current', tone: 'slate', detail: 'Refresh status to check this retained member. Selection reasons below are historical.' }
      : reviewPresentation(observed);
  return { blinded, review };
}

// The ONE plain line answering "why is this here?": [grade chip] [caveat ·] [reason].
export function SelectionWhy({ note = null, ...props }) {
  const { paper, selectionPolicy } = props;
  const { blinded, review } = evidenceView(props);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1">
      {review.text && <span className={`inline-flex max-w-full items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${CHIP_TONE[review.tone] || CHIP_TONE.slate}`}>{review.text}</span>}
      <p className="text-xs text-slate-700">
        {note && <span className={note.warn ? 'text-amber-800' : 'text-slate-500'}>{note.text} </span>}
        {selectionLine(paper, selectionPolicy, blinded)}
      </p>
    </div>
  );
}

// The card's single disclosure: the owner's secondary reading (children: authors,
// abstract, icon legend) above the honest selection details.
export function SelectionDetails({ children, ...props }) {
  const { paper, selectionPolicy } = props;
  const { blinded, review } = evidenceView(props);
  return <div className="[&_summary]:text-slate-600">
    <Disclosure summary="more">
      <div className="text-xs text-slate-600 leading-relaxed break-words">
        {children}
        {blinded ? <p>Review and selection details are withheld to keep this selection blinded.</p> : <>
          {props.retained && <p>Reasons recorded when this paper was selected; not a new ranking.</p>}
          <p>{roleReason(paper, selectionPolicy)}</p>
          {selectionPolicy !== ANALYZED && Array.isArray(paper.why) && paper.why.length > 0 && <ul className="mt-1 list-disc pl-4">
            {paper.why.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>}
          <p className="mt-2">{review.detail}</p>
          {selectionPolicy !== ANALYZED && <Observations paper={paper} selectionPolicy={selectionPolicy} />}
        </>}
      </div>
    </Disclosure>
  </div>;
}
