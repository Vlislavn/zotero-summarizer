import { useLayoutEffect, useRef, useState } from 'react';
import { DECISIONS, DECISION_BTN, DECISION_HOVER, DECISION_TINT } from '../decisionVocab.js';
import { pretty } from '../../utils/priorityLabels.js';

// The verdict of the open paper (the Today vocabulary). After the open paper changes the same pill sits
// under the finger of the previous tap, so taps are ignored for QUIET_MS (H03); the pills stay focusable.
export const QUIET_MS = 500;

export default function VerdictBar({ itemKey, title, current = null, ready = true, hidden = false, onDecide }) {
  const [quiet, setQuiet] = useState(false);
  const shown = useRef(itemKey);
  useLayoutEffect(() => {
    if (shown.current === itemKey) return undefined;
    shown.current = itemKey;
    setQuiet(true);
    const timer = setTimeout(() => setQuiet(false), QUIET_MS);
    return () => clearTimeout(timer);
  }, [itemKey]);
  const off = quiet || !ready;
  return (
    <div role="group" aria-label={`Verdict for ${title}`} data-testid="verdict-bar"
      className={`${hidden ? 'hidden' : 'flex flex-wrap'} gap-2`}>
      {DECISIONS.map(([priority, label]) => (
        <button key={priority} type="button" aria-label={pretty(priority)} aria-pressed={current === priority} aria-disabled={off || undefined}
          onClick={() => { if (!off) onDecide(priority); }}
          className={`${DECISION_BTN} ${DECISION_TINT[priority]} ${DECISION_HOVER[priority]} ${off ? 'opacity-40' : ''}`}>{label}</button>
      ))}
    </div>
  );
}
