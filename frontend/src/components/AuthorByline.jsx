import { useState } from 'react';

// Renders "Smith J (h=42), Lee P, Park K" given the uniform authors
// shape returned by the backend: `[{name: string, h_index: number|null}]`.
//
// `source` ('feed' | 'note' | 'library') controls the empty-state.
// `quiet=true` collapses the empty state to nothing (used on Today, where
// most feed cards have no authors and a placeholder per row is noise).
// `limit` (paper page) shows the first N names + the last author, with the
// rest one tap away — a 100-author list must not push the review off screen.
function Author({ author }) {
  return (
    <>
      {author.name}
      {author.h_index != null && (
        <span
          className="ml-1 text-[10px] text-emerald-700 font-medium"
          title={author.last ? 'Last author’s own h-index (OpenAlex)' : 'Author h-index (OpenAlex)'}
        >
          ({author.last ? 'last author ' : ''}h={author.h_index})
        </span>
      )}
      {author.h_unknown && (
        <span className="ml-1 text-[10px] text-slate-500" title="OpenAlex could not resolve the last author’s h-index">
          (last author, h unknown)
        </span>
      )}
    </>
  );
}

export default function AuthorByline({ authors = [], source = 'library', quiet = false, limit = 0 }) {
  const [expanded, setExpanded] = useState(false);
  if (!authors || authors.length === 0) {
    if (quiet) return null;
    const msg = source === 'feed'
      ? '(authors not in feed metadata)'
      : source === 'note'
        ? '(parent paper has no authors)'
        : source === 'csv_stub'
          ? '(no authors in stored row)'
          : '(no authors listed in Zotero)';
    return <span className="text-xs text-slate-400 italic">{msg}</span>;
  }
  // Folding fewer than three names would cost a tap and save nothing.
  const foldable = limit > 0 && authors.length > limit + 3;
  const folded = foldable && !expanded;
  const lead = folded ? authors.slice(0, limit) : authors;
  const hidden = authors.length - limit - 1;
  const comma = <span className="text-slate-400">, </span>;
  return (
    <span className="text-xs text-slate-700">
      {lead.map((a, i) => (
        <span key={`${a.name}-${i}`}>
          <Author author={a} />
          {i < lead.length - 1 && comma}
        </span>
      ))}
      {folded && <><span className="text-slate-400">, … </span><Author author={authors.at(-1)} /></>}
      {foldable && (
        <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}
          className="ml-2 text-forest-800 underline underline-offset-2 hover:text-forest-700">
          {expanded ? 'Show fewer' : `+${hidden} more`}
        </button>
      )}
    </span>
  );
}
