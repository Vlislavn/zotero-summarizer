import { useEffect, useRef, useState } from 'react';
import { rowNodes } from './readNextModel.js';

const FOLLOW_MS = 250;  // the open paper follows j / k after this much calm: one review-detail read, not one per press

// Keys act on `selKey`: the open paper, else the row last moved to with j / k. With a paper open j / k
// move the outline at once and the paper follows FOLLOW_MS later (`guarded` keeps an unsaved note).
export default function useRowSelection({ listRef, openKey, openState, isDesktop, guarded }) {
  const [selKey, setSelKey] = useState(null);
  const followTimer = useRef(null);
  const moved = useRef(false);  // the selection came from a key: bring its row into view
  useEffect(() => {
    clearTimeout(followTimer.current);
    if (openKey) setSelKey(openKey);
  }, [openKey]);
  useEffect(() => () => clearTimeout(followTimer.current), []);
  useEffect(() => {
    if (!moved.current) return;
    moved.current = false;
    const row = rowNodes(listRef).find((n) => n.dataset.rowKey === selKey);
    row?.scrollIntoView?.({ block: 'nearest' });
    if (!openKey || isDesktop) row?.querySelector('button')?.focus({ preventScroll: true });  // a phone's Sheet makes the list inert
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selKey]);
  function select(key) {
    moved.current = true;
    setSelKey(key);
  }
  function moveSelection(delta) {
    const keys = rowNodes(listRef).map((n) => n.dataset.rowKey);
    if (!keys.length) return;
    const next = keys[Math.min(Math.max(keys.indexOf(selKey) + delta, 0), keys.length - 1)];
    select(next);
    clearTimeout(followTimer.current);
    if (openKey && next !== openKey) followTimer.current = setTimeout(() => guarded(() => openState.open(next)), FOLLOW_MS);
  }
  return { selKey, setSelKey, select, moveSelection };
}
