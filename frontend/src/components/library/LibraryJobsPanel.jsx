import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PredictionsBar from './PredictionsBar.jsx';
import { scoresText } from './libraryStatus.js';
import { formatShortDate } from './shared.jsx';

// The page's jobs, one disclosure: a small control at the right edge of the status row (the page puts both in one
// flex-wrap row) that opens the panel under it (E6 option B): Scores · Zotero · Top-pick reviews.
// A job's result is not shown here but in the status line (the page's `message` is its receipt), so a
// result appears where the page already speaks, not at the bottom. A review run opens the panel by itself,
// because its progress and Stop live here.
const BUTTON = 'inline-flex min-h-11 items-center rounded-lg border px-3 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50';
const PLAIN = `${BUTTON} border-slate-300 bg-white text-slate-700 hover:bg-slate-50`;
const STALE = `${BUTTON} border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100`;
const TERMINAL = ['error', 'done_empty', 'ready'];

const Job = ({ title, children }) => (
  <section aria-label={title} className="py-2">
    <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-500">{title}</h3>
    {children}
  </section>
);

// ctx: what libraryStatus() reads (model, scores, unscored, pending). zotero: null while Zotero is not connected, else
// {pending, syncing, fetching, syncedAt: {tags, ranks}, onSend, onFetch}. picks: the PredictionsBar props.
// `unavailable`: why the jobs cannot run right now (empty when they can).
export default function LibraryJobsPanel({ ctx, onRescore, zotero, picks, unavailable = '' }) {
  const picksRunning = Boolean(picks.autoActive || picks.stopping || picks.fleetStatus?.status === 'running');
  const [open, setOpen] = useState(false);
  useEffect(() => { if (picksRunning) setOpen(true); }, [picksRunning]);
  const picksShown = picksRunning || picks.coolCount > 0 || TERMINAL.includes(picks.fleetStatus?.status);
  const unscored = ctx.modelReady && ctx.unscored > 0 ? ` · ${ctx.unscored} not scored` : '';
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="library-jobs-panel"
        aria-label="Library jobs"
        onClick={() => setOpen((v) => !v)}
        className="ml-auto inline-flex min-h-11 shrink-0 cursor-pointer select-none items-center gap-1 rounded-lg border border-slate-200 bg-slate-50/60 px-2 text-xs sm:gap-1.5 sm:px-3 font-semibold text-slate-600"
      >
        <span className="sm:hidden">Jobs</span>
        <span className="hidden sm:inline">Library jobs</span>
        <span aria-hidden="true" className="text-slate-400">{open ? '▾' : '▸'}</span>
      </button>
      <div
        id="library-jobs-panel"
        hidden={!open}
        data-testid="library-jobs"
        className="basis-full rounded-lg border border-slate-200 bg-slate-50/60 px-3 text-xs"
      >
        <fieldset disabled={Boolean(unavailable)} className="divide-y divide-slate-200/60 pb-1">
          {unavailable && <p className="py-2 text-slate-600">{unavailable}</p>}
          <Job title="Scores">
            <p className="text-slate-600">{scoresText(ctx)}{unscored}</p>
            {ctx.modelReady && (
              <button type="button" onClick={onRescore} disabled={ctx.computing} className={`mt-1 ${ctx.scoresStale ? STALE : PLAIN}`}
                title="Recompute relevance scores for your whole library against the current model. The first full scan can take a few minutes; re-scans are fast.">
                {ctx.computing ? 'Scoring…' : 'Rescore'}
              </button>
            )}
          </Job>
          {zotero && (
            <Job title="Zotero">
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={zotero.onSend} disabled={zotero.syncing} className={PLAIN}
                  title="Asks first, then the whole chain: rescore the library if the model changed since, then write zs:rel/<band> relevance tags AND stamp the Call-Number ranks (zr0001…) into Zotero. Backup-first; needs Zotero closed (you'll be asked again otherwise). Then sort the Call Number column in Zotero.">
                  {zotero.syncing ? 'Sending…' : 'Send to Zotero…'}
                </button>
                <button type="button" onClick={zotero.onFetch} disabled={zotero.syncing || zotero.fetching} className={PLAIN}
                  title="Download the open-access full-text PDF for every library paper that has a source link but no PDF, and attach it to Zotero. Skips papers that already have a PDF. Backs up first; runs with Zotero closed; PDFs upload to zotero.org on the next sync.">
                  {zotero.fetching ? 'Fetching…' : 'Fetch missing PDFs…'}
                </button>
              </div>
              {zotero.pending > 0 && (
                <Link to="/ops?tab=pending" className="mt-1 inline-flex min-h-11 items-center font-semibold text-teal-700 underline underline-offset-2">
                  {`${zotero.pending} Zotero change${zotero.pending === 1 ? '' : 's'} to apply ›`}
                </Link>
              )}
              {(zotero.syncedAt.tags || zotero.syncedAt.ranks) && (
                <p className="mt-1 text-slate-500">
                  Last sent — relevance tags: {formatShortDate(zotero.syncedAt.tags) || 'never'} · Call-Number ranks: {formatShortDate(zotero.syncedAt.ranks) || 'never'}
                </p>
              )}
            </Job>
          )}
          {picksShown && <Job title="Top-pick reviews"><PredictionsBar {...picks} /></Job>}
        </fieldset>
      </div>
    </>
  );
}
