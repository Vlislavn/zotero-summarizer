import { useCallback, useEffect, useRef } from 'react';
import useKeyboardNav from '../../hooks/useKeyboardNav.js';
import { nextUndecided } from '../../hooks/useLibraryVerdict.js';
import { DECISIONS } from '../decisionVocab.js';
import { priorOf } from './readNextModel.js';

// Keys (F-final-plan §3.4): 1–4 are the pills left to right; the rows and the pills keep the keys after a click focused them.
const KEY_PRIORITY = Object.fromEntries(DECISIONS.map(([priority], i) => [i + 1, priority]));
const KEYS_FROM = '[data-row-key] button, [data-verdict-bar] button';

// The list's keyboard (j k 1–4 z Enter Esc /) and the row tap. 1–4 wait until the paper has followed a j / k
// (they would hit the old one): with a paper open they go through `decideRef`, VerdictBar's gate.
export default function useRowKeys({
  sel, openKey, openState, isDesktop, guarded, flow, queueItems, items, selectMode, onToggleItem, onFocusSearch,
}) {
  const { selKey, select, moveSelection } = sel;
  const decideRef = useRef(null);  // VerdictBar's gate: the same quiet period and readiness as a pill tap
  const toggleRef = useRef(null);
  function decideByKey(priority) {
    if (openKey) {
      if (selKey === openKey) decideRef.current?.(priority);
      return;
    }
    flow.verdict(selKey, priority, { prior: priorOf(queueItems, selKey) });
    const next = nextUndecided(items, selKey);
    if (next) select(next);
  }
  useKeyboardNav({
    onPrev: () => moveSelection(-1),
    onNext: () => moveSelection(1),
    onAction: decideByKey,
    actionKeys: KEY_PRIORITY,
    hasSelection: Boolean(selKey) && !selectMode,
    onEnter: selKey && selKey !== openKey ? () => toggleRef.current(selKey) : undefined,
    onEscape: openKey ? () => guarded(openState.close) : undefined,
    onUndo: flow.toast?.kind === 'saved' ? flow.undo : undefined,
    onSlash: onFocusSearch && (!openKey || isDesktop) ? onFocusSearch : undefined,
    allowFrom: KEYS_FROM,
    allowModal: '[data-testid="library-detail"]',  // the phone detail Sheet; any other modal (a confirmation) silences the list
  });

  // Rows are memoised: they get one stable callback that reads the latest state. A tap opens
  // that paper's detail (the open one closes it); in Select mode it ticks the row instead.
  const toggleRow = useCallback((key) => toggleRef.current(key), []);
  useEffect(() => {
    toggleRef.current = (key) => {
      if (selectMode) onToggleItem?.(key);
      else guarded(() => (key === openKey ? openState.close() : openState.open(key)));
    };
  });
  return { decideRef, toggleRow };
}
