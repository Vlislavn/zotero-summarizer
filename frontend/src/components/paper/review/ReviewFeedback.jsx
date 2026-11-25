import { Fold } from './primitives.jsx';
import { PART_LABELS, REVIEW_FEEDBACK_CHIPS, WHOLE } from './reviewFeedbackFields.js';

const CHIP_LABELS = Object.fromEntries(REVIEW_FEEDBACK_CHIPS);
const CHECK_TEXT = { confirmed: 'confirmed', rejected: 'not confirmed', unclear: 'unclear' };

// «Your feedback on this review (N)»: what the owner already said, one row per part, below the review. Edit reopens
// the part's panel; Withdraw removes it (the saved package keeps a «withdrawn» record). `checks` is what was concluded
// after looking at the paper (check.json), shown as «Checked: …». Nothing to list → nothing rendered.
export default function ReviewFeedback({ entries, checks, onEdit, onWithdraw, withdrawing }) {
  const fields = Object.keys(entries).sort((a, b) => (a === WHOLE) - (b === WHOLE));
  if (!fields.length) return null;
  const summary = fields.map((field) => PART_LABELS[field]).join(' · ');
  return <Fold id="review-feedback" label={`Your feedback on this review (${fields.length})`} summary={summary}>
    <ul className="space-y-3">
      {fields.map((field) => {
        const { chips, note } = entries[field];
        const check = checks?.[field];
        return <li key={field} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 text-[14px] leading-snug text-slate-800">
          <div className="min-w-0">
            <div className="font-medium">{PART_LABELS[field]}</div>
            {chips.length > 0 && <div className="text-slate-600">{chips.map((id) => CHIP_LABELS[id]).filter(Boolean).join(' · ')}</div>}
            {note && <div className="break-words text-slate-600">“{note}”</div>}
            {check && <div className="text-forest-800">Checked: {CHECK_TEXT[check.status]}{check.note ? ` — ${check.note}` : ''}</div>}
          </div>
          <div className="flex">
            <button type="button" onClick={() => onEdit(field)} className="min-h-11 px-2 text-[13px] text-forest-800 underline underline-offset-2">Edit</button>
            <button type="button" onClick={() => onWithdraw(field)} disabled={withdrawing}
              className="min-h-11 px-2 text-[13px] text-rose-700 underline underline-offset-2 disabled:opacity-50">Withdraw</button>
          </div>
        </li>;
      })}
    </ul>
  </Fold>;
}
