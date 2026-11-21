import { fetchCollections } from '../api/libraryApi.js';
import { collectionOptionLabel } from '../pages/pendingHelpers.js';
import useCollections from '../hooks/useCollections.js';

// Shared Zotero collection <select> (value = collection key). Used by Today's
// add-to-library batch bar and Targeted Search's per-result "Add to library".
// Empty value = the server default ("Inbox"). Fetches the tree once on mount and
// reuses pendingHelpers' flatten + label so the option list matches the Pending page.
export default function CollectionPicker({ value, onChange, disabled = false, className = '' }) {
  // Degrades to Inbox-only if the tree can't load — the picker still adds to the default.
  const flat = useCollections(fetchCollections);
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      title="Target Zotero collection"
      className={className || 'min-w-0 max-w-full text-xs px-2 py-1 rounded-lg border border-slate-300 bg-white text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500'}
    >
      <option value="">Inbox (default)</option>
      {flat.map((entry) => (
        <option key={entry.key} value={entry.key}>{collectionOptionLabel(entry)}</option>
      ))}
    </select>
  );
}
