import { useEffect, useState } from 'react';
import { isAutoVerdict } from '../../../utils/priorityLabels.js';
import { DECISIONS, DECISION_BTN, DECISION_HOVER, DECISION_TINT, DECISION_WORD, NEG_CHIPS, POS_CHIPS } from '../../decisionVocab.js';

// Sticky compact verdict bar for the paper page on phones (lg:hidden). It brings
// the decision onto the first screen (the full rail editor is ~screens down), using
// the SAME must/should/could/skip vocabulary and one-tap reason chips as the Today
// card (Jakob's Law — one decision surface the owner already knows). It drives the
// SAME verdict path as the rail (`usePaperReview` → golden verdict); a fresh
// decision owns its reasons (starts with none), chips update the saved comment.
function chipComment(ids, set) {
  const label = Object.fromEntries(set);
  return ids.map((id) => label[id]).filter(Boolean).join(', ');
}

// `verdict` is the object usePaperReview returns: { existing, onSubmit, onDelete,
// submitting, submitError, deleting }.
export default function MobileVerdictBar({ itemKey, verdict }) {
  const saved = verdict.existing?.user_priority || null;
  const [decision, setDecision] = useState(saved);
  const [chips, setChips] = useState([]);
  const [touched, setTouched] = useState(false);
  useEffect(() => { setDecision(saved); setChips([]); setTouched(false); }, [itemKey, saved]);

  const busy = verdict.submitting || verdict.deleting;
  const auto = !touched && isAutoVerdict(verdict.existing);
  const chipSet = decision === 'dont_read' ? NEG_CHIPS : POS_CHIPS;

  const decide = (priority) => {
    if (busy) return;
    setTouched(true); setDecision(priority); setChips([]);
    verdict.onSubmit({ user_priority: priority, comment: '' });  // Fresh decision starts with no reason.
  };
  const toggleChip = (id) => {
    if (busy || !decision) return;
    const next = chips.includes(id) ? chips.filter((c) => c !== id) : [...chips, id];
    setTouched(true); setChips(next);
    verdict.onSubmit({ user_priority: decision, comment: chipComment(next, chipSet) });
  };
  const undo = () => { if (busy) return; setTouched(true); setDecision(null); setChips([]); verdict.onDelete(); };

  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 px-3 py-2 backdrop-blur lg:hidden">
      {decision ? (
        <div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span className="font-medium text-slate-800">{auto ? 'Auto' : 'Yours'}: {DECISION_WORD[decision]}</span>
            <button type="button" onClick={undo} disabled={busy}
              className="min-h-10 text-forest-800 underline underline-offset-2 disabled:opacity-50">Undo</button>
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {chipSet.map(([id, label]) => (
              <button key={id} type="button" onClick={() => toggleChip(id)} aria-pressed={chips.includes(id)} disabled={busy}
                className={`min-h-10 rounded-lg border px-2.5 text-[12px] disabled:opacity-50 ${
                  chips.includes(id) ? 'border-forest-800 bg-forest-800 text-white'
                    : decision === 'dont_read' ? 'border-rose-200 bg-white text-slate-600'
                      : 'border-emerald-200 bg-white text-slate-600'}`}>{label}</button>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {DECISIONS.map(([priority, label]) => (
            <button key={priority} type="button" onClick={() => decide(priority)} disabled={busy}
              className={`${DECISION_BTN} ${DECISION_TINT[priority]} ${DECISION_HOVER[priority]}`}>{label}</button>
          ))}
        </div>
      )}
      {verdict.submitError && <p role="alert" className="mt-1 text-[11px] text-rose-700">{verdict.submitError}</p>}
    </div>
  );
}
