import { feedLabel } from '../../utils/feedText.js';

function SelectAll({ picked, busy, onSelect }) {
  const { selectable, selected, allSelected } = picked;
  return <label className="flex min-h-11 cursor-pointer select-none items-center gap-1.5 sm:min-h-9">
    <input type="checkbox" checked={allSelected} disabled={busy}
      onChange={() => onSelect(allSelected ? new Set() : new Set(selectable.map(member => member.paper.item_id)))}
      className="h-4 w-4 rounded border-slate-300 text-forest-800 focus:ring-forest-600" />
    {selected.length ? `${selected.length} selected` : 'Select all'}
  </label>;
}

function FeedSelect({ members, feedNames, feedFilter, onFeedFilter }) {
  return <select value={feedFilter} onChange={event => onFeedFilter(event.target.value)}
    aria-label="Feed source" className="min-h-11 min-w-[10rem] max-w-full flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-slate-700 sm:max-w-xs sm:min-h-9">
    <option value="">All sources ({members.length})</option>
    {feedFilter && !feedNames.includes(feedFilter) && <option value={feedFilter}>{feedFilter} (0)</option>}
    {feedNames.map(name => <option key={name} value={name}>{feedLabel(name)} ({members.filter(member => member.paper.feed_name === name).length})</option>)}
  </select>;
}

// «Select all» and the feed-source filter; hidden when neither applies.
export default function SlateToolbar({ members, picked, feedNames, feedFilter, onFeedFilter, busy, onSelect }) {
  const hasSelectable = picked.selectable.length > 0;
  const hasFeeds = feedNames.length > 1 || feedFilter;
  if (!hasSelectable && !hasFeeds) return null;
  return <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-0 text-xs text-slate-600">
    {hasSelectable && <SelectAll picked={picked} busy={busy} onSelect={onSelect} />}
    {hasFeeds && <FeedSelect members={members} feedNames={feedNames} feedFilter={feedFilter} onFeedFilter={onFeedFilter} />}
  </div>;
}
