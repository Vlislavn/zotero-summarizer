import { useEffect, useState } from 'react';
import { rowByline, rowSignals } from './readNextCopy.js';
import InlineAnnotate from './InlineAnnotate.jsx';
import VerdictBar from './VerdictBar.jsx';
import Sheet from '../ui/Sheet.jsx';
import usePaperReview from '../../hooks/usePaperReview.js';

// The paper opened from the Read-next list. Phones: a full-screen Sheet over the still-mounted
// list. ≥1024 px: the right half of a split view. The header comes from the list row, so the
// paper shows at once; `review-detail` (InlineAnnotate) fills in the rest.
// An exit with an unsaved note (✕, Close, scrim, Esc, ‹ ›, another row) asks inside the detail:
// the parent passes `leave` = the exit waiting for the answer; the announcement lives here too,
// because an open Sheet silences live regions in other portals; the Undo toast rides in the Sheet too.
// The verdict bar: sticky at the bottom of the sheet, the first row of the split view; hidden while a text
// field in the detail has focus (the keyboard needs the room).

function LeaveQuestion({ onStay, onLeave }) {
  return (
    <div role="alert" className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[13px] text-amber-900">
      <p>You have unsaved notes on this paper. Leave without saving them?</p>
      <div className="mt-2 flex gap-2">
        <button type="button" autoFocus onClick={onStay}
          className="min-h-11 rounded-md bg-teal-700 px-3 text-[13px] font-semibold text-white hover:bg-teal-800">Keep editing</button>
        <button type="button" onClick={onLeave}
          className="min-h-11 rounded-md border border-slate-300 bg-white px-3 text-[13px] text-slate-700 hover:bg-slate-50">Leave</button>
      </div>
    </div>
  );
}

function Stepper({ position, total, onPrev, onNext, notInList }) {
  const btn = 'min-h-11 min-w-11 rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40';
  return (
    <div className="sticky top-0 z-10 -mx-4 mb-2 flex items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-1">
      <span className="text-xs text-slate-500">
        {position ? `${position} of ${total}` : notInList ? 'Not in the current list' : ''}
      </span>
      {position && (
        <span className="flex gap-1">
          <button type="button" className={btn} aria-label="Previous paper" disabled={!onPrev} onClick={onPrev}>‹</button>
          <button type="button" className={btn} aria-label="Next paper" disabled={!onNext} onClick={onNext}>›</button>
        </span>
      )}
    </div>
  );
}

const TEXT_FIELD = 'input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]),textarea,[contenteditable=""],[contenteditable="true"]';
const isTextField = (el) => Boolean(el?.matches?.(TEXT_FIELD));

export default function LibraryDetail({
  itemKey, item = null, sheet, inList, nav, collections, onClose, onStep, leave, onStay, onLeave,
  onSaved, onQueueRefresh, onNotesDirtyChange, why = null, goalsStrong = false, modelReady = true,
  onVerdict, surface, toast = null, caughtUp = false,
}) {
  const review = usePaperReview(itemKey, { onSaved, onQueueRefresh, surface });
  const [typing, setTyping] = useState(false);
  const focusWatch = { onFocus: (e) => setTyping(isTextField(e.target)), onBlur: (e) => setTyping(isTextField(e.relatedTarget)) };
  // What Undo restores: the saved verdict with its comment once the detail has loaded.
  const prior = review.detail || !item?.user_priority
    ? review.detail?.verdict ?? null : { user_priority: item.user_priority, comment: '' };
  const ready = Boolean(review.detail || review.error || (item && !item.user_priority));
  useEffect(() => { setTyping(false); }, [itemKey]);
  const name = item?.title || 'Paper details';
  const title = caughtUp ? 'All caught up in this list' : name;
  const { byline, year } = item ? rowByline(item) : {};
  const signals = item ? rowSignals(item, { goalsStrong, modelReady }) : [];
  const spot = nav.position ? `, ${nav.position} of ${nav.total}` : '';
  const bar = !caughtUp && (
    <VerdictBar itemKey={itemKey} title={name} current={prior?.user_priority ?? null} ready={ready} hidden={typing}
      onDecide={(priority) => onVerdict(priority, prior)} />
  );
  const body = (
    <>
      <p className="sr-only" role="status" aria-live="polite">{`${title}${spot}`}</p>
      <Stepper
        position={nav.position} total={nav.total} notInList={!inList}
        onPrev={nav.prev && (() => onStep(nav.prev))} onNext={nav.next && (() => onStep(nav.next))}
      />
      {leave && <LeaveQuestion onStay={onStay} onLeave={onLeave} />}
      {caughtUp ? (
        <button type="button" onClick={onClose}
          className="mt-4 min-h-11 rounded-md bg-teal-700 px-4 text-[13px] font-semibold text-white hover:bg-teal-800">Back to list</button>
      ) : (
        <>
          {item && (
            <div className="mb-2 space-y-0.5 text-xs text-slate-500">
              <p>{[byline, year, item.has_pdf && 'PDF'].filter(Boolean).join(' · ')}</p>
              <p className="text-[11px]">{signals.map((s) => s.text).join(' · ')}</p>
            </div>
          )}
          {!sheet && <div className="mb-2">{bar}</div>}
          <InlineAnnotate
            itemKey={itemKey}
            review={review}
            collections={collections}
            showTitle={!item}
            onNotesDirtyChange={onNotesDirtyChange}
          />
          {why && <div className="pb-2 pt-1">{why}</div>}
        </>
      )}
      {sheet && (toast || bar) && (
        <div className={`sticky -bottom-4 z-10 -mx-4 -mb-4 border-t border-slate-200 bg-white px-4 pb-2 pt-2 ${typing && !toast ? 'hidden' : ''}`}>
          {toast && <div className="mb-2">{toast}</div>}
          {bar}
        </div>
      )}
    </>
  );
  if (sheet) return <Sheet title={title} onClose={onClose}><div data-testid="library-detail" {...focusWatch}>{body}</div></Sheet>;
  return (
    <aside
      data-testid="library-detail"
      {...focusWatch}
      aria-label={`Review ${title}`}
      className="lg:w-1/2 lg:shrink-0 lg:sticky lg:top-2 lg:self-start lg:max-h-[86vh] lg:overflow-auto rounded-xl border border-teal-200 bg-white px-4 pb-4"
    >
      <div className="flex items-start justify-between gap-2 pt-3">
        <h2 className="text-base font-medium text-slate-900">{title}</h2>
        <button type="button" onClick={onClose} aria-label="Close detail"
          className="min-h-11 min-w-11 shrink-0 rounded text-slate-500 hover:bg-slate-100 hover:text-slate-800">✕</button>
      </div>
      {body}
    </aside>
  );
}
