import { useState } from 'react';

// One single-select list with a search box, folded under its current value: the Collection and Tag
// facets. A pick applies at once (no «apply» step). `options`: [{value, label, count, depth}], in the
// order to show; the «all» row stays on top while searching.
const ROW = 'flex min-h-11 cursor-pointer items-center gap-2 rounded px-2 text-sm text-slate-700 hover:bg-slate-100';

export default function FilterPicker({ name, noun, options, value, onChange, allLabel, unknownLabel }) {
  const [query, setQuery] = useState('');
  const needle = query.trim().toLowerCase();
  const shown = needle ? options.filter((o) => o.label.toLowerCase().includes(needle)) : options;
  const known = options.find((o) => o.value === value);
  const current = value ? (known?.label ?? unknownLabel) : allLabel;
  const row = (o) => (
    <label key={o.value || 'all'} className={ROW} style={{ paddingLeft: 8 + (o.depth || 0) * 14 }}>
      <input type="radio" name={`filter-${name}`} checked={o.value === value} onChange={() => onChange(o.value)}
        className="h-4 w-4 shrink-0 border-slate-300 text-teal-600 focus:ring-teal-500" />
      <span className="min-w-0 break-words">{o.label}</span>
      {typeof o.count === 'number' && <span className="mono ml-auto shrink-0 text-xs text-slate-500">{o.count}</span>}
    </label>
  );
  return (
    <details className="border-b border-slate-200 py-1">
      <summary className="flex min-h-11 cursor-pointer select-none list-none items-center justify-between gap-3 text-sm [&::-webkit-details-marker]:hidden">
        <span className="font-semibold text-slate-700">{name}</span>
        <span className="truncate text-slate-600">{current} <span aria-hidden="true">›</span></span>
      </summary>
      <input type="search" aria-label={`Search ${noun}`} placeholder={`Search ${noun}…`}
        value={query} onChange={(e) => setQuery(e.target.value)}
        className="mb-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500" />
      <div role="radiogroup" aria-label={name} className="slim-scroll max-h-60 overflow-y-auto pb-1">
        {row({ value: '', label: allLabel })}
        {shown.map(row)}
        {needle && !shown.length && <p className="px-2 py-2 text-sm text-slate-500">No {noun} match “{query.trim()}”.</p>}
      </div>
    </details>
  );
}
