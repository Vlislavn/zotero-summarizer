import { useCallback, useEffect, useRef, useState } from 'react';
import { NEG_CHIPS, POS_CHIPS } from '../components/decisionVocab.js';
import { pretty } from '../utils/priorityLabels.js';
import { UNDO_WINDOW_MS } from '../utils/verdictUndo.js';

// A Today decision, start to finish (#206): the card collapses out of the list at once and ONE toast
// offers Undo for UNDO_WINDOW_MS, plus the optional reason chips that match the decision. Same shape
// as useLibraryVerdict, but it writes through Today's own verdict endpoints (`decide` and `undo` come from
// useTodayVisit). A positive decision also adds the paper to Zotero (#207): the reply says whether the Add
// parked (Zotero open) and the toast says so; Undo withdraws the label only, so an Add that already
// reached Zotero stays and the toast tells so.
// `left` holds the ids hidden until the members refresh confirms the decision; Undo or a failed save brings the card back.
// What the owner must know about a positive decision's Zotero Add; nothing to add when it succeeded or was not tried.
function addNote(reply) {
  if (!reply?.add_attempted || reply.added_to_library) return '';
  if (reply.add_status === 'zotero_pending' || reply.add_status === 'zotero_unavailable') {
    return 'Not in Zotero yet: Zotero must be closed to add it. The banner above says what to do.';
  }
  return reply.add_error ? `Not added to Zotero: ${reply.add_error}` : '';
}

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
    if (next?.kind === 'saved' || next?.kind === 'info') timer.current = setTimeout(() => setToast(null), UNDO_WINDOW_MS);
  }, []);
  const setLeftTo = useCallback((id, hidden) => setLeft(prev => {
    const next = new Set(prev);
    if (hidden) next.add(id); else next.delete(id);
    return next;
  }), []);

  const decideCard = useCallback((paper, priority) => {
    const title = paper.title || 'this paper';
    const accepted = decide(paper.item_id, priority, [], {
      onSuccess: reply => {
        const note = addNote(reply);
        if (note) setToast(current => current?.kind === 'saved' && current.itemId === paper.item_id ? { ...current, note } : current);
      },
      onError: () => {
        setLeftTo(paper.item_id, false);
        show({ kind: 'failed', message: `Couldn’t save “${title}”`, retry: () => decideCard(paper, priority) });
      },
    });
    if (!accepted) return false;
    setLeftTo(paper.item_id, true);
    show({ kind: 'saved', title, message: `${pretty(priority)}: “${title}”`, itemId: paper.item_id, priority, reasons: [] });
    return true;
  }, [decide, setLeftTo, show]);

  const finishUndo = useCallback(itemId => {
    setLeftTo(itemId, false);
    show(null);
  }, [setLeftTo, show]);
  const runUndo = useCallback(current => undo(current.itemId, {
    onSuccess: reply => {
      if (reply?.item_stays_in_zotero) show({ kind: 'info', message: `Decision withdrawn. “${current.title}” stays in Zotero.` });
    },
  }), [undo, show]);
  const undoCard = useCallback(() => {
    const current = shown.current;
    if (current?.kind !== 'saved') return;
    if (runUndo(current)) finishUndo(current.itemId); else setQueuedUndo(true); // the decision is still being saved
  }, [runUndo, finishUndo]);
  useEffect(() => {
    const current = shown.current;
    if (queuedUndo && !busy && current?.kind === 'saved' && runUndo(current)) finishUndo(current.itemId);
  }, [queuedUndo, busy, runUndo, finishUndo]);

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
