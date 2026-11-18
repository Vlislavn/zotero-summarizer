import { useContext, useEffect, useRef, useState } from 'react';
import { QueryClientContext } from '@tanstack/react-query';
import { NEG_CHIPS } from '../components/decisionVocab.js';
import { isDecided } from '../components/library/readNextCopy.js';
import { UNDO_WINDOW_MS, decide } from '../utils/verdictUndo.js';
import { pretty } from '../utils/priorityLabels.js';

// A verdict set from the Library detail, start to finish (F-final-plan T13), without re-reading the
// queue: write it (utils/verdictUndo) → patch the row in place (`onRowsChange`) → move the detail to the
// next undecided paper → show the toast with Undo. A failed save puts the row back and offers Retry.
// `env` is read live: { queueItems, items, openState, notesDirty, guarded, surface, onRowsChange, onWrite }.
const asDecided = (priority) => ({ user_priority: priority, decided: true });
const asBefore = (row) => ({ user_priority: row.user_priority, decided: row.decided });
const chipLabel = (id) => NEG_CHIPS.find(([key]) => key === id)?.[1];
const NOTE_BLOCKS_ADVANCE = 'Unsaved note — save it, or leave this paper, to continue.';

// The next row without a verdict of its own and not read, after `key` (wrapping to the top of the list).
function nextUndecided(items, key) {
  const at = items.findIndex((it) => it.item_key === key);
  const rest = [...items.slice(at + 1), ...items.slice(0, at)];
  return rest.find((it) => !isDecided(it) && !it.read)?.item_key ?? null;
}

export default function useLibraryVerdict(env) {
  const live = useRef(env);
  live.current = env;
  const queryClient = useContext(QueryClientContext);  // absent in a bare list render: nothing cached to refresh
  const [toast, setToast] = useState(null);
  const [caughtUp, setCaughtUp] = useState(false);
  const shown = useRef(null);
  const timer = useRef(null);
  shown.current = toast;
  useEffect(() => { setCaughtUp(false); }, [env.openState.key]);
  useEffect(() => () => clearTimeout(timer.current), []);

  function show(next) {
    clearTimeout(timer.current);
    setToast(next);
    if (next?.kind === 'saved') timer.current = setTimeout(() => setToast(null), UNDO_WINDOW_MS);
  }
  // The cached detail of a paper whose verdict changed is stale; `prior` is read from it.
  const refreshDetail = (key) => queryClient?.invalidateQueries({ queryKey: ['paper-detail', key] });

  function verdict(key, priority, { prior = null, advance = true } = {}) {
    const { queueItems, items, openState, notesDirty, surface, onRowsChange, onWrite } = live.current;
    const row = queueItems.find((it) => it.item_key === key);
    const title = row?.title || 'this paper';
    const moves = advance && openState.key === key && row && !isDecided(row) && !row.read
      && items.some((it) => it.item_key === key);
    const next = moves && !notesDirty ? nextUndecided(items, key) : undefined;
    const handle = decide(key, priority, { prior, surface });
    if (row) onRowsChange([[key, asDecided(priority)]]);
    onWrite(handle.done);
    show({
      kind: 'saved', message: `${pretty(priority)}: “${title}”`, handles: [handle], keys: [key],
      restore: row ? [[key, asBefore(row)]] : [],
      reasons: priority === 'dont_read' ? [] : undefined,
      note: moves && notesDirty ? NOTE_BLOCKS_ADVANCE : undefined,
    });
    if (next === null) setCaughtUp(true);
    else if (next) openState.open(next);
    handle.done.then(() => refreshDetail(key), () => {
      if (row) live.current.onRowsChange([[key, asBefore(row)]]);
      setCaughtUp(false);
      show({ kind: 'failed', message: `Couldn’t save “${title}”`, retry: () => verdict(key, priority, { prior, advance: false }) });
    });
  }

  function undo() {
    const current = shown.current;
    if (current?.kind !== 'saved') return;
    const { openState, guarded, onRowsChange, onWrite } = live.current;
    const undone = Promise.all(current.handles.map((handle) => handle.undo()));
    onWrite(undone);
    undone.then(() => current.keys.forEach(refreshDetail), () => {});  // a failed Undo is corrected by the reconcile
    onRowsChange(current.restore);
    show(null);
    setCaughtUp(false);
    if (current.keys.length === 1 && openState.key) guarded(() => openState.open(current.keys[0]));
  }

  function toggleReason(id) {
    const current = shown.current;
    if (current?.kind !== 'saved' || !current.reasons) return;
    const reasons = current.reasons.includes(id) ? current.reasons.filter((r) => r !== id) : [...current.reasons, id];
    current.handles[0].setReasons(reasons.map(chipLabel));
    setToast({ ...current, reasons });
  }

  return { toast, caughtUp, verdict, undo, toggleReason, dismiss: () => show(null) };
}
