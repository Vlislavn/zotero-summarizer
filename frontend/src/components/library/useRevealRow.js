import { useEffect, useRef, useState } from 'react';
import { REVEAL_STEP, rowNodes } from './readNextModel.js';

const REVEAL_AT = 0.3;  // a row brought into view lands this far down the screen: the top third, where reading starts
const FLASH_MS = 2000;  // the row you came back from stays highlighted this long

// What the list shows of itself: the reveal in chunks (the backend returns the whole ranked library, but
// only a bounded slice is mounted, so a ~2,400-item list paints fast and stays scrollable) and the two folds.
export function useListReveal(filterSig) {
  const [visibleCount, setVisibleCount] = useState(REVEAL_STEP);
  const [mustAll, setMustAll] = useState(false);  // «Show all N» of your own Must read (this visit)
  const [decidedOpen, setDecidedOpen] = useState(false);  // the «Decided by you» block
  // A filter change should jump back to the top of the (now different) list — a
  // client filter doesn't remount the view (server filters do, via the key), so
  // reset the reveal here instead.
  useEffect(() => { setVisibleCount(REVEAL_STEP); }, [filterSig]);
  return { visibleCount, setVisibleCount, mustAll, setMustAll, decidedOpen, setDecidedOpen };
}

// The open paper's row must be on screen when the detail closes and when the page is entered with
// `?open=KEY` (a link, a reload, Back from the full review): make sure it is rendered (the list reveals in
// chunks and folds groups), then scroll to it and mark it for a moment. Closing also focuses it.
// Returns the key of the row to highlight.
export default function useRevealRow({ listRef, openKey, items, groups, mustPreview, reveal }) {
  const { setVisibleCount, setMustAll, setDecidedOpen } = reveal;
  const [returnKey, setReturnKey] = useState(null);  // {key, focus}: the row to bring into view
  const [flashKey, setFlashKey] = useState(null);
  function revealRow(key, focus) {
    const inMust = groups ? groups.must.findIndex((it) => it.item_key === key) : -1;
    const inTodo = (groups ? groups.todo : items).findIndex((it) => it.item_key === key);
    if (inMust >= mustPreview) setMustAll(true);
    if (inTodo >= 0) setVisibleCount((n) => Math.max(n, inTodo + 1 + REVEAL_STEP));  // rows below it too, so the page can scroll it up
    if (groups?.decided.some((it) => it.item_key === key)) setDecidedOpen(true);
    setReturnKey({ key, focus });
  }
  const lastOpen = useRef(openKey);
  useEffect(() => {
    const was = lastOpen.current;
    lastOpen.current = openKey;
    if (was && !openKey) revealRow(was, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openKey]);
  const entryKey = useRef(openKey);  // the paper open when the list mounted; revealed once the loaded list has it
  useEffect(() => {
    if (entryKey.current !== openKey) entryKey.current = null;
    if (!entryKey.current || !items.some((it) => it.item_key === openKey)) return;
    entryKey.current = null;
    revealRow(openKey, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openKey, items]);
  useEffect(() => {
    if (!returnKey) return;
    const row = rowNodes(listRef).find((n) => n.dataset.rowKey === returnKey.key);
    setReturnKey(null);
    if (!row) return;
    const { top, bottom } = row.getBoundingClientRect();
    if (top < 0 || bottom > window.innerHeight) window.scrollBy?.({ top: top - window.innerHeight * REVEAL_AT });
    if (returnKey.focus) row.querySelector('button')?.focus({ preventScroll: true });
    setFlashKey(returnKey.key);
  }, [returnKey, listRef]);
  useEffect(() => {
    if (!flashKey) return undefined;
    const timer = setTimeout(() => setFlashKey(null), FLASH_MS);
    return () => clearTimeout(timer);
  }, [flashKey]);
  return flashKey;
}
