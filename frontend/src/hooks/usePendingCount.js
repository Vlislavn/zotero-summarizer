import { useEffect, useState } from 'react';
import { fetchPendingCount } from '../api/pendingApi.js';

// The Zotero change queue size (the number Ops → Pending changes lists), not the
// device outbox. Refetched when the connection settles, a sync lands or the route
// changes; null while offline/unknown so the header says nothing instead of "0".
const CHANGED = 'zs:pending-changed';
// The Pending page calls this after apply/reject so the header number follows the list.
export const notifyPendingChanged = () => window.dispatchEvent(new Event(CHANGED));

export default function usePendingCount(reachable, ...refetchOn) {
  const [count, setCount] = useState(null);
  const [bump, setBump] = useState(0);
  useEffect(() => {
    const onChanged = () => setBump((n) => n + 1);
    window.addEventListener(CHANGED, onChanged);
    return () => window.removeEventListener(CHANGED, onChanged);
  }, []);
  useEffect(() => {
    if (!reachable) { setCount(null); return undefined; }
    let current = true;
    fetchPendingCount().then((n) => current && setCount(n), () => current && setCount(null));
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reachable, bump, ...refetchOn]);
  return count;
}
