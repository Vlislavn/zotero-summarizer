import { NEG_CHIPS, chipClass } from '../decisionVocab.js';

// What the last verdict did, one line, with the way back (F-final-plan §3.2): «Not interested: “Title” [Undo]».
// A Not-interested verdict adds one scrolling row of reason chips. A failed save says so and offers Retry.
const BTN = 'min-h-11 shrink-0 px-3 text-[13px] font-medium text-forest-800 underline underline-offset-2';

// `chips` — the reason set offered with `toast.reasons` (Library: Not interested only; Today also offers the positive set).
export default function UndoToast({ toast, onUndo, onReason, onDismiss, chips = NEG_CHIPS }) {
  if (!toast) return null;
  const failed = toast.kind === 'failed';
  return (
    <div role={failed ? 'alert' : 'status'} data-testid="undo-toast"
      className={`rounded-lg border bg-white px-3 ${failed ? 'border-rose-300' : 'border-slate-300'}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-[13px] text-slate-800">{toast.message}</p>
        {failed ? (
          <span className="flex shrink-0">
            {toast.retry && <button type="button" className={BTN} onClick={toast.retry}>Retry</button>}
            <button type="button" className={BTN} onClick={onDismiss}>Dismiss</button>
          </span>
        ) : <button type="button" className={BTN} onClick={onUndo}>Undo</button>}
      </div>
      {toast.note && <p className="pb-2 text-xs text-amber-800">{toast.note}</p>}
      {toast.reasons && (
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-2" aria-label="Why? (optional)" role="group">
          {chips.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={toast.reasons.includes(id)} onClick={() => onReason(id)}
              className={`${chipClass(toast.reasons.includes(id))} shrink-0 whitespace-nowrap`}>{label}</button>
          ))}
        </div>
      )}
    </div>
  );
}
