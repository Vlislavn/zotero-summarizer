import { useEffect, useState } from 'react';
import { flattenCollections } from '../pages/pendingHelpers.js';

// Flat Zotero collection list for a <select>, fetched once on mount via the caller's
// `fetchTree` (a stable api function). Degrades to [] when the tree can't load —
// logged, not swallowed. Ignores a response that lands after unmount.
export default function useCollections(fetchTree) {
  const [flat, setFlat] = useState([]);
  useEffect(() => {
    let alive = true;
    fetchTree()
      .then((data) => { if (alive) setFlat(flattenCollections(data?.items || [])); })
      .catch((err) => console.error('useCollections: failed to load collections', err));
    return () => { alive = false; };
  }, [fetchTree]);
  return flat;
}
