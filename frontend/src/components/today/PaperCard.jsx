// Today cull card: relevance is separate from review evidence. Selection
// observations explain the existing server order; rendering never rescores.

import { useEffect, useRef, useState } from 'react';
import AuthorByline from '../AuthorByline.jsx';
import PaperFactsCard, { CardMore } from '../card/PaperFactsCard.jsx';
import Icon from '../ui/Icon.jsx';
import {
  DECISIONS, DECISION_BTN, DECISION_HOVER, DECISION_TINT, DECISION_WORD, NEG_CHIPS, POS_CHIPS,
} from '../decisionVocab.js';
import useCardExposure from '../../hooks/useCardExposure.js';
import { trackToday } from '../../utils/todayEvents.js';
import OutdatedReviewNote from './OutdatedReviewNote.jsx';
import { SelectionDetails, SelectionWhy } from './SelectionEvidence.jsx';
import { scoreToBand } from '../../utils/relevanceBands.js';
import { Link } from 'react-router-dom';
import { recommendationView, todayPaperUrl } from '../../utils/todayVisit.js';
import { cleanAbstract, feedLabel } from '../../utils/feedText.js';

// Relevance-band → left accent in the verdict's own Forest grade (Von Restorff):
// treasures full-weight, trash desaturated. Keyed by relevanceBands.scoreToBand names.
const BAND_CARD_CLS = {
  must_read: 'border border-slate-200 border-l-4 border-l-forest-800 bg-white',
  should_read: 'border border-slate-200 border-l-4 border-l-sage-300 bg-white',
  could_read: 'border border-slate-200 border-l-4 border-l-sage-200 bg-white',
  dont_read: 'border border-slate-200 border-l-2 border-l-slate-300 bg-slate-50 opacity-70',
};
const PILL = 'inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium';
// One Today DECISION vocabulary (#37/T10): must / should / could / skip — shared by
// the recommendation hint (analyzed picks) and the owner's own decision buttons.
// The relevance band stays a descriptive MAGNITUDE (a different axis); reusing
// skip for "weak relevance" would conflate a magnitude with a verdict.
const RELEVANCE_WORD = {
  must_read: 'top pick', should_read: 'strong', could_read: 'moderate', dont_read: 'weak',
};
const PRIORITY_WORD = { must_read: 'must', should_read: 'should', could_read: 'could' };
const CONSUMPTION = {
  original: 'Better read the original.',
  selected_parts: 'Key parts are enough.',
  brief: 'The brief is enough.',
};

// Everything the owner does NOT need to decide: authors, venue, the abstract, and
// the card's icon legend. Lives behind the card's ONE “more” disclosure.
// The review's checklist rides on the member payload (`quality.rubric`); only a current review decides the icons.
const reviewRubric = (paper) => (paper.selection_evidence?.review?.quality_origin === 'current_entry' ? paper.quality?.rubric : null) || null;

function MoreBody({ paper, authors }) {
  const abstract = cleanAbstract(paper.abstract);
  return (
    <div className="mb-3 space-y-2">
      <div className="space-y-0.5">
        <AuthorByline authors={authors} source="feed" quiet />
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-slate-500">
          {paper.venue && <span className="italic">{paper.venue}</span>}
          {paper.pub_year && <span>{paper.pub_year}</span>}
          {paper.feed_name && <span>· {feedLabel(paper.feed_name)}</span>}
        </div>
      </div>
      {abstract && <p className="whitespace-pre-line text-xs leading-relaxed text-slate-600">{abstract}</p>}
      {paper.stable_feed_key && <CardMore cardKey={paper.stable_feed_key} rubric={reviewRubric(paper)} />}
    </div>
  );
}

function parseAuthorsString(s) {
  if (!s || typeof s !== 'string') return [];
  return s
    .split(s.includes(';') ? ';' : ',')
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ name, h_index: null }));
}

export default function PaperCard({ paper, selected, onToggleSelect, llmEnabled = true, selectionPolicy,
  visitId, memberState, currentFacts, statusFresh, busy, onRetry, onDecide, onUndo, decisionPending = false }) {
  const authors = parseAuthorsString(paper.authors);
  // Only the LAST author carries an h-index, and it is that author's own (0 is real;
  // missing = unknown). The max over authors is never attributed to a name.
  if (authors.length > 0) {
    const h = Number.isInteger(paper.last_author_h_index) ? paper.last_author_h_index : null;
    authors[authors.length - 1] = { ...authors[authors.length - 1], h_index: h, h_unknown: h === null, last: true };
  }
  const displayedScore = paper.selection_evidence?.stored?.composite_recorded === false
    || !Number.isFinite(paper.composite_score) ? null : paper.composite_score;
  const recommended = recommendationView(paper, memberState?.recommendation);
  // Outdated-but-valid review (same PDF): recommendation is kept, badged with Regenerate.
  const freshness = statusFresh ? currentFacts?.review_freshness : paper.review_freshness;
  const outdatedReview = Boolean(paper.stable_feed_key) && recommended && !recommended.withdrawn
    && freshness?.status === 'outdated';
  // Exploration cards are random picks from just below the bar (logged for later calibration).
  const exploration = paper.role === 'exploration';
  const band = exploration ? null : recommended ? (recommended.withdrawn ? null : recommended.priority) : scoreToBand(displayedScore);
  const cardCls = exploration ? 'border border-dashed border-mist-300 bg-mist-50/40'
    : BAND_CARD_CLS[band] || 'border border-slate-200 bg-white';
  // One quiet clause for the "why" line: the random-pick caveat, a withdrawal, or how much to read.
  const whyNote = exploration ? { text: 'Random check for calibration, not a recommendation.', warn: false }
    : recommended && (recommended.withdrawn || CONSUMPTION[recommended.consumption])
      ? { text: recommended.withdrawn || CONSUMPTION[recommended.consumption], warn: Boolean(recommended.withdrawn) } : null;
  const cardRef = useRef(null);
  useCardExposure(cardRef, visitId, paper);
  const track = (kind, target = null) => trackToday(visitId, {
    item_key: paper.stable_feed_key || paper.item_key, item_id: paper.item_id, kind, target,
  });

  // Per-card decision (#37): one tap = verdict, one optional tap = reason. The
  // saved decision comes from the current facts; local state gives instant feedback.
  const savedPriority = currentFacts?.user_priority || null;
  const [decision, setDecision] = useState(savedPriority);
  const [chips, setChips] = useState([]);
  useEffect(() => { setDecision(savedPriority); if (!savedPriority) setChips([]); }, [savedPriority]);
  const canDecide = typeof onDecide === 'function' && !decisionPending && !busy
    && (memberState ? memberState.selectable || Boolean(savedPriority) : true);
  const reviewReady = memberState ? memberState.reviewReady : paper.review_ready === true;
  const handleDecide = priority => {
    if (!canDecide) return;
    if (priority !== 'dont_read' && !reviewReady) return;  // Owner-kept review gate on Zotero writes.
    setDecision(priority); setChips([]);
    onDecide(paper.item_id, priority, []);  // Fresh decision owns its reasons: starts with none (NULL).
  };
  const toggleChip = id => {
    const next = chips.includes(id) ? chips.filter(chip => chip !== id) : [...chips, id];
    setChips(next);
    onDecide(paper.item_id, decision, next);
  };
  const handleUndo = () => { setDecision(null); setChips([]); onUndo?.(paper.item_id); };
  const chipSet = decision === 'dont_read' ? NEG_CHIPS : POS_CHIPS;

  // The title opens the full review (the paper page accepts feed keys and Zotero keys alike);
  // the publisher page stays one quiet «Source ↗» link.
  const reviewKey = paper.stable_feed_key || paper.item_key;
  const sourceUrl = paper.url || (paper.doi ? `https://doi.org/${paper.doi}` : null);
  const titleCls = 'text-sm font-medium text-slate-900';
  const titleNode = reviewKey
    ? (
      <Link
        to={visitId ? todayPaperUrl(reviewKey, visitId) : `/paper/${encodeURIComponent(reviewKey)}`}
        onClick={() => track('open_review')}
        className={`${titleCls} hover:text-forest-800 underline-offset-2 hover:underline`}
      >
        {paper.title || '(untitled)'}
      </Link>
    )
    : <span className={titleCls}>{paper.title || '(untitled)'}</span>;

  return (
    <article
      ref={cardRef}
      className={`rounded-xl p-3 sm:p-4 transition-colors ${cardCls} ${
        selected ? 'border-forest-600' : ''
      }`}
    >
      <div className="flex items-start gap-3">
        <label className="-m-2 flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center">
          <input
            type="checkbox"
            checked={selected}
            disabled={busy || (memberState && !memberState.selectable)}
            onChange={() => onToggleSelect(paper.item_id)}
            aria-label={`Select ${paper.title || 'paper'}`}
            className="h-4 w-4 rounded border-slate-300 text-forest-800 focus:ring-forest-600 cursor-pointer"
          />
        </label>
        <div className="min-w-0 flex-1">
          <header>
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <span className="inline-flex flex-wrap items-center gap-1.5">
                <span
                  className={`${PILL} ${exploration ? 'border-mist-300 bg-mist-100 text-mist-800'
                    : band ? `${DECISION_TINT[band]}` : 'border-slate-200 bg-slate-100 text-slate-600'}`}
                  title={exploration ? 'Random pick from just below your bar, kept for calibration'
                    : recommended ? 'Reading recommendation recorded for this shortlist'
                      : 'Relevance to you, not a scientific-quality judgment'}
                >
                  {exploration ? 'Random check' : recommended?.withdrawn ? 'Recommendation withdrawn'
                    : recommended ? `Recommended: ${PRIORITY_WORD[band]}`
                      : `Relevance: ${band ? RELEVANCE_WORD[band] : 'unscored'}`}
                </span>
              </span>
              {sourceUrl && (
                <a href={sourceUrl} target="_blank" rel="noreferrer noopener"
                  onClick={() => track('open_link', 'title')} onAuxClick={() => track('open_link', 'title')}
                  className="inline-flex min-h-11 items-center text-[12px] text-slate-500 underline-offset-2 hover:text-forest-800 hover:underline sm:min-h-8">
                  Source ↗
                </a>
              )}
            </div>
            <div className="mt-1 break-words text-[15px] leading-snug">{titleNode}</div>
          </header>

          {outdatedReview && <OutdatedReviewNote itemKey={paper.stable_feed_key} />}

          {paper.stable_feed_key && <PaperFactsCard cardKey={paper.stable_feed_key} variant="today" rubric={reviewRubric(paper)} />}

          <SelectionWhy paper={paper} selectionPolicy={selectionPolicy}
            currentStatus={currentFacts} statusFresh={statusFresh} retained={Boolean(visitId)}
            note={whyNote} />
          {memberState?.text && <p role="status" className="mt-2 text-xs text-slate-600">{memberState.text}</p>}
          {memberState?.canRetry && <button type="button" onClick={onRetry} disabled={busy}
            className="min-h-11 text-xs text-forest-800 underline disabled:opacity-50">
            Retry unfinished action
          </button>}

          {typeof onDecide === 'function' && (decision ? (
            <div className="mt-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className={`inline-flex h-11 items-center gap-1.5 rounded-lg border px-3 text-[13px] font-medium sm:h-8 ${DECISION_TINT[decision]} border-forest-800`}>
                  <Icon name="check" size={14} strokeWidth={2} />Decision: {DECISION_WORD[decision]}
                </span>
                <button type="button" onClick={handleUndo} disabled={decisionPending}
                  className="min-h-11 px-1 text-xs text-forest-800 underline underline-offset-2 disabled:opacity-50 sm:min-h-8">Undo</button>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">Reason (optional, one tap)</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {chipSet.map(([id, label]) => <button key={id} type="button" onClick={() => toggleChip(id)}
                  aria-pressed={chips.includes(id)} disabled={decisionPending}
                  className={`inline-flex min-h-11 items-center gap-1 rounded-lg border px-2.5 text-[12px] disabled:opacity-50 sm:min-h-8 ${
                    chips.includes(id) ? 'border-forest-800 bg-sage-100 text-forest-800' : 'border-slate-300 bg-white text-slate-600 hover:border-slate-400'}`}>
                  {chips.includes(id) && <Icon name="check" size={12} strokeWidth={2} />}{label}</button>)}
              </div>
            </div>
          ) : (
            <div className="mt-3">
              <div className="flex flex-wrap gap-2">
                {DECISIONS.map(([priority, label]) => {
                  const blocked = priority !== 'dont_read' && !reviewReady;
                  return <button key={priority} type="button" onClick={() => handleDecide(priority)}
                    disabled={!canDecide || blocked}
                    title={blocked ? 'Needs a review first — open the paper to Generate one' : undefined}
                    className={`${DECISION_BTN} ${DECISION_TINT[priority]} ${DECISION_HOVER[priority]}`}>{label}</button>;
                })}
              </div>
              {!reviewReady && (!memberState || memberState.reviewKnown) && paper.stable_feed_key && <p className="mt-1.5 text-[11px] text-amber-800">
                must/should/could need a review. {llmEnabled
                  ? <>Open the paper to generate one; skip works right away.</>
                  : <><Link className="underline" to="/settings">Enable AI</Link> for reviews; skip works right away.</>}
              </p>}
            </div>
          ))}

          {typeof onDecide !== 'function' && paper.stable_feed_key
            && (memberState ? memberState.selectable && !memberState.reviewReady : !paper.review_ready) && (
            <p className="mt-2 text-xs text-amber-800">
              {llmEnabled ? 'Generate a review before adding.' : <>
                AI review is off. <Link className="underline" to="/settings">Enable AI</Link> before adding,
                or import this paper manually in Zotero.
              </>}
            </p>
          )}
          <div className="mt-2">
            <SelectionDetails paper={paper} selectionPolicy={selectionPolicy}
              currentStatus={currentFacts} statusFresh={statusFresh} retained={Boolean(visitId)}>
              <MoreBody paper={paper} authors={authors} />
            </SelectionDetails>
          </div>
        </div>
      </div>
    </article>
  );
}
