// Renders "Smith J (h=42), Lee P, Park K" given the uniform authors
// shape returned by the backend: `[{name: string, h_index: number|null}]`.
//
// `source` ('feed' | 'note' | 'library') controls the empty-state.
// `quiet=true` collapses the empty state to nothing (used on Today, where
// most feed cards have no authors and a placeholder per row is noise).
export default function AuthorByline({ authors = [], source = 'library', quiet = false }) {
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
  return (
    <span className="text-xs text-slate-700">
      {authors.map((a, i) => (
        <span key={`${a.name}-${i}`}>
          {a.name}
          {a.h_index != null && (
            <span
              className="ml-1 text-[10px] text-emerald-700 font-semibold"
              title="Top author h-index from OpenAlex"
            >
              (h={a.h_index})
            </span>
          )}
          {i < authors.length - 1 && <span className="text-slate-400">, </span>}
        </span>
      ))}
    </span>
  );
}
