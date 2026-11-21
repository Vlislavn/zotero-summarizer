import { Link } from 'react-router-dom';

// Back link + Prev/Next through the ranked list the paper was opened from.
export default function PaperNavBar({ fromToday, cameFrom, navigate, navIdx, total, prevKey, nextKey, goToKey }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0 text-[13px] text-slate-500">
      <Link
        to={fromToday ? '/today' : '/library'}
        onClick={!fromToday && cameFrom ? (e) => { e.preventDefault(); navigate(-1); } : undefined}
        className="inline-flex min-h-11 items-center hover:text-forest-800">{fromToday ? '← Back to Today' : '← Back'}</Link>
      {navIdx >= 0 && (
        <>
          <span className="text-slate-300" aria-hidden="true">·</span>
          <button
            type="button"
            onClick={() => goToKey(prevKey)}
            disabled={!prevKey}
            className="inline-flex min-h-11 items-center px-1 hover:text-forest-800 disabled:opacity-40 sm:min-h-0"
          >
            ‹ Prev
          </button>
          <span className="tabular-nums text-slate-400">{navIdx + 1} / {total}</span>
          <button
            type="button"
            onClick={() => goToKey(nextKey)}
            disabled={!nextKey}
            className="inline-flex min-h-11 items-center px-1 hover:text-forest-800 disabled:opacity-40 sm:min-h-0"
          >
            Next ›
          </button>
          <span className="hidden text-slate-400 sm:inline" title="Keyboard: j = next, k = previous">j / k</span>
        </>
      )}
    </div>
  );
}
