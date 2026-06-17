// Shared helpers for the Library "Read next" surface. (Quality-grade colours now
// live in paper/review/tones.js — the single source for the review vocabulary.)

export function formatShortDate(value) {
  if (!value) return '';
  const s = String(value);
  if (/^\d{4}/.test(s)) return s.slice(0, 10);
  return s;
}

export function truncateAuthors(authors) {
  if (!authors) return '';
  if (typeof authors === 'string') return authors.length > 60 ? `${authors.slice(0, 60)}…` : authors;
  if (Array.isArray(authors)) {
    const joined = authors
      .map((a) => (typeof a === 'string' ? a : (a?.name || `${a?.first_name || ''} ${a?.last_name || ''}`.trim())))
      .filter(Boolean)
      .join(', ');
    return joined.length > 60 ? `${joined.slice(0, 60)}…` : joined;
  }
  return '';
}

export function StatusBanner({ message, isError }) {
  if (!message) return null;
  const cls = isError
    ? 'bg-rose-50 border-rose-200 text-rose-800'
    : 'bg-emerald-50 border-emerald-200 text-emerald-800';
  return <div className={`my-2 p-2 rounded-lg border text-xs ${cls}`}>{message}</div>;
}
