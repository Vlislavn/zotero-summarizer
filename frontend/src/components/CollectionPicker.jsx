import { useEffect, useState } from 'react';
import { fetchCollections } from '../api/libraryApi.js';
import { flattenCollections, collectionOptionLabel } from '../pages/pendingHelpers.js';

// Shared Zotero collection <select> (value = collection key). Used by Today's
// add-to-library batch bar and Targeted Search's per-result "Add to library".
// Empty value = the server default ("Inbox"). Fetches the tree once on mount and
// reuses pendingHelpers' flatten + label so the option list matches the Pending page.
export default function CollectionPicker({ value, onChange, disabled = false, className = '' }) {
  const [flat, setFlat] = useState([]);
  useEffect(() => {
    let alive = true;
    fetchCollections()
      .then((tree) => { if (alive) setFlat(flattenCollections(tree)); })
      // Degrade to Inbox-only if the tree can't load — the picker still adds to the
      // default. Logged, not swallowed.
      .catch((err) => console.error('CollectionPicker: failed to load collections', err));
    return () => { alive = false; };
  }, []);
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      title="Target Zotero collection"
      className={className || 'text-xs px-2 py-1 rounded-lg border border-slate-300 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500'}
    >
      <option value="">Inbox (default)</option>
      {flat.map((entry) => (
        <option key={entry.key} value={entry.key}>{collectionOptionLabel(entry)}</option>
      ))}
    </select>
  );
}
