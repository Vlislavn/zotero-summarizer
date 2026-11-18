import { useCallback, useEffect, useRef, useState } from 'react';
import { NEG_CHIPS, POS_CHIPS } from '../components/decisionVocab.js';
import { pretty } from '../utils/priorityLabels.js';
import { UNDO_WINDOW_MS } from '../utils/verdictUndo.js';

// A Today decision, start to finish (#206): the card collapses out of the list at once and ONE toast
// offers Undo for UNDO_WINDOW_MS, plus the optional reason chips that match the decision. Same shape
// as useLibraryVerdict, but it writes through Today's own verdict endpoints (label only; `decide` and
// `undo` come from useTodayVisit), not the golden verdict that also creates the Zotero item.
// `left` holds the ids hidden until the members refresh confirms the decision; Undo or a failed save brings the card back.
export default function useTodayDecisionToast({ decide, undo, busy }) {
  const [toast, setToast] = useState(null);
  const [left, setLeft] = useState(() => new Set());
  const [queuedUndo, setQueuedUndo] = useState(false);
  const timer = useRef(null);
  const shown = useRef(null);
  shown.current = toast;
  useEffect(() => () => clearTimeout(timer.current), []);

  const show = useCallback(next => {
    clearTimeout(timer.current);
    setQueuedUndo(false);
    setToast(next);
    if (next?.kind === 'saved') timer.current = setTimeout(() => setToast(null), UNDO_WINDOW_MS);
  }, []);
  const setLeftTo = useCallback((id, hidden) => setLeft(prev => {
    const next = new Set(prev);
    if (hidden) next.add(id); else next.delete(id);
    return next;
  }), []);

  const decideCard = useCallback((paper, priority) => {
    const title = paper.title || 'this paper';
    const accepted = decide(paper.item_id, priority, [], {
      onError: () => {
        setLeftTo(paper.item_id, false);
        show({ kind: 'failed', message: `Couldn’t save “${title}”`, retry: () => decideCard(paper, priority) });
      },
    });
    if (!accepted) return false;
    setLeftTo(paper.item_id, true);
    show({ kind: 'saved', message: `${pretty(priority)}: “${title}”`, itemId: paper.item_id, priority, reasons: [] });
    return true;
  }, [decide, setLeftTo, show]);

  const finishUndo = useCallback(itemId => {
    setLeftTo(itemId, false);
    show(null);
  }, [setLeftTo, show]);
  const undoCard = useCallback(() => {
    const current = shown.current;
    if (current?.kind !== 'saved') return;
    if (undo(current.itemId)) finishUndo(current.itemId); else setQueuedUndo(true); // the decision is still being saved
  }, [undo, finishUndo]);
  useEffect(() => {
    const current = shown.current;
    if (queuedUndo && !busy && current?.kind === 'saved' && undo(current.itemId)) finishUndo(current.itemId);
  }, [queuedUndo, busy, undo, finishUndo]);

  const toggleReason = useCallback(id => {
    const current = shown.current;
    if (current?.kind !== 'saved') return;
    const reasons = current.reasons.includes(id) ? current.reasons.filter(r => r !== id) : [...current.reasons, id];
    if (decide(current.itemId, current.priority, reasons)) setToast({ ...current, reasons });
  }, [decide]);

  return {
    toast, left, decide: decideCard, undo: undoCard, toggleReason, dismiss: () => show(null),
    chips: toast?.priority === 'dont_read' ? NEG_CHIPS : POS_CHIPS,
  };
}
