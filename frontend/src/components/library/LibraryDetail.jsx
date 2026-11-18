import InlineAnnotate from './InlineAnnotate.jsx';
import Sheet from '../ui/Sheet.jsx';

// The paper opened from the Read-next list. Phones: a full-screen Sheet over the still-mounted
// list. ≥1024 px: the right half of a split view. The title comes from the list row.
// An exit with an unsaved note (✕, Close, scrim, Esc, ‹ ›, another row) asks inside the detail:
// the parent passes `leave` = the exit waiting for the answer; the announcement lives here too,
// because an open Sheet silences live regions in other portals.

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

export default function LibraryDetail({
  itemKey, item = null, sheet, inList, nav, collections, onClose, onStep, leave, onStay, onLeave,
  onSaved, onQueueRefresh, onNotesDirtyChange, why = null,
}) {
  const title = item?.title || 'Paper details';
  const spot = nav.position ? `, ${nav.position} of ${nav.total}` : '';
  const body = (
    <>
      <p className="sr-only" role="status" aria-live="polite">{`${title}${spot}`}</p>
      <Stepper
        position={nav.position} total={nav.total} notInList={!inList}
        onPrev={nav.prev && (() => onStep(nav.prev))} onNext={nav.next && (() => onStep(nav.next))}
      />
      {leave && <LeaveQuestion onStay={onStay} onLeave={onLeave} />}
      <InlineAnnotate
        itemKey={itemKey}
        collections={collections}
        showTitle={!item}
        onSaved={onSaved}
        onQueueRefresh={onQueueRefresh}
        onNotesDirtyChange={onNotesDirtyChange}
      />
      {why && <div className="pb-2 pt-1">{why}</div>}
    </>
  );
  if (sheet) return <Sheet title={title} onClose={onClose}><div data-testid="library-detail">{body}</div></Sheet>;
  return (
    <aside
      data-testid="library-detail"
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
