// Shared helpers for the Library "Read next" surface. (Quality-grade colours now
// live in paper/review/tones.js — the single source for the review vocabulary.)
import { humanizeError } from '../../utils/humanizeError.js';

export function formatShortDate(value) {
  if (!value) return '';
  const s = String(value);
  if (/^\d{4}/.test(s)) return s.slice(0, 10);
  return s;
}

// Relative "time ago" — a deep review from 3 months back is a different signal
// than one from today, so the review recency reads as "reviewed 3d ago" (exact
// date on hover). Falls back to the short date for unparseable input.
export function timeAgo(value) {
  if (!value) return '';
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return formatShortDate(value);
  const secs = Math.max(0, (Date.now() - then) / 1000);
  const DAY = 86400;
  if (secs < 3600) return 'just now';
  if (secs < DAY) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < DAY * 30) return `${Math.floor(secs / DAY)}d ago`;
  if (secs < DAY * 365) return `${Math.floor(secs / (DAY * 30))}mo ago`;
  return `${Math.floor(secs / (DAY * 365))}y ago`;
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

// Canonical status/error banner for the whole app. Carries the a11y role +
// aria-live so screen readers announce it; pages import this instead of
// re-defining their own (they used to, with weaker or no a11y).
export function StatusBanner({ message, isError, tone }) {
  if (!message) return null;
  // `tone` ('error' | 'warn' | 'success') wins; `isError` is the legacy boolean.
  // 'warn' (caution/ochre) is for RECOVERABLE conditions — e.g. "Zotero is busy,
  // close it and retry" — so a retryable hiccup isn't painted max-severity clay
  // (which trains banner-blindness). Clay/error stays reserved for fatal.
  const t = tone || (isError ? 'error' : 'success');
  const cls = t === 'error'
    ? 'bg-rose-50 border-rose-200 text-rose-800'
    : t === 'warn'
      ? 'bg-amber-50 border-amber-200 text-amber-900'
      : 'bg-emerald-50 border-emerald-200 text-emerald-800';
  return (
    <div
      role={t === 'error' ? 'alert' : 'status'}
      aria-live="polite"
      className={`my-2 p-2 rounded-lg border text-xs ${cls}`}
    >
      {message}
    </div>
  );
}

// One mutually-exclusive explanation for a review that could not get full text.
// Shared by the full paper page and the compact review section so recovery copy
// cannot drift between surfaces.
export function FullTextAccessNotice({ deep }) {
  if (!deep?.needs_pdf) return null;
  const cls = "rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] leading-relaxed text-amber-800";
  if (deep.acquire_outcome === 'browser_extra_unavailable') {
    return (
      <div className={cls} role="status">
        <span className="font-semibold">Library access is unavailable in this installation.</span>{' '}
        <a href="/settings" className="font-medium text-indigo-700 hover:underline">Open Settings</a>{' '}
        to install browser support, then generate again.
      </div>
    );
  }
  if (deep.needs_login && deep.login_url) {
    return (
      <div className={cls} role="status">
        <span className="font-semibold">Your library session could not access this paper.</span>{' '}
        <a href={deep.login_url} target="_blank" rel="noopener noreferrer" className="font-medium text-indigo-700 hover:underline">
          Open the publisher page
        </a>{' '}
        to sign in, then generate again.
      </div>
    );
  }
  return (
    <div className={cls} role="status">
      <span className="font-semibold">No readable full text found.</span>{' '}
      Open access and the configured acquisition methods did not return a usable copy.
    </div>
  );
}

// Canonical error banner. Runs the value through humanizeError so any thrown
// shape (Error, {message|detail}, string) renders as a friendly sentence —
// never "[object Object]". Pages import this instead of re-defining it.
export function ErrorBanner({ error, title = 'Error' }) {
  if (!error) return null;
  return (
    <div role="alert" className="my-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
      <span className="font-semibold">{title}:</span> {humanizeError(error)}
    </div>
  );
}
