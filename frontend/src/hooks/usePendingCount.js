import { useEffect, useState } from 'react';
import { fetchPendingCount } from '../api/pendingApi.js';

// The Zotero change queue size (the number Ops → Pending changes lists), not the
// device outbox. Refetched when the connection settles, a sync lands or the route
// changes; null while offline/unknown so the header says nothing instead of "0".
export default function usePendingCount(reachable, ...refetchOn) {
  const [count, setCount] = useState(null);
  useEffect(() => {
    if (!reachable) { setCount(null); return undefined; }
    let current = true;
    fetchPendingCount().then((n) => current && setCount(n), () => current && setCount(null));
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reachable, ...refetchOn]);
  return count;
}
