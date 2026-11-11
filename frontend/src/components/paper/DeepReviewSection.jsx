import { useState } from 'react';
import useDeepReviewRunner from '../../hooks/useDeepReviewRunner.js';
import Spinner from '../ui/Spinner.jsx';
import { FullTextAccessNotice } from '../library/shared.jsx';
import PaperReview from './review/PaperReview.jsx';
import ReviewFailure from './ReviewFailure.jsx';
import ReviewModelStatus from './ReviewModelStatus.jsx';

// "92" -> "1m 32s", "8" -> "8s". Used for the live elapsed + ETA readout so the
// running review reports real progress instead of a fixed "~1–2 min" guess.
function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

// Per-paper deep review. Renders the cached review natively via <PaperReview>
// (one design language, flat hierarchy — no nested digest card, no iframe) and
// owns the run control: a button that runs a full-text LLM digest and polls
// until done, then calls onDone() to refetch. Pre-empts a missing PDF, an
// unreachable model, and an already-running review. Shared by Library + Annotate.
export default function DeepReviewSection({ itemKey, deep, onDone, hasPdf = true, compact = false }) {
  const [focusPrompt, setFocusPrompt] = useState('');
  const runner = useDeepReviewRunner(itemKey, { deep, onDone });
  const { status, error, llmAvailable, online, running, run, retryStatus, checkingStatus } = runner;
  const reviewed = deep && !deep.needs_pdf && (deep.digest || deep.quality || (deep.goal_summaries || []).length);
  return (
    <div className="space-y-3">
      {!running && !(compact && reviewed) && <ReviewModelStatus runner={runner} />}
      <FullTextAccessNotice deep={deep} />

      {reviewed && <PaperReview deep={deep} compact={compact} />}

      {!hasPdf && !reviewed && (
        <div className="text-[12px] text-slate-500">
          No PDF attached — the review will try to fetch the full text first
          (open access, PubMed Central, then your library session).
        </div>
      )}
      {/* In the compact Library card a FINISHED review needs no run controls —
          textarea + Re-run are pure clutter on a done paper, so the whole block is
          hidden when compact && reviewed && !running. It still shows when there's
          no review yet (to build one) or while one is running. Re-run-with-focus
          lives in the full review / Annotate. */}
      {!(compact && reviewed && !running) && (
      <div className="space-y-2">
          <textarea
            value={focusPrompt}
            onChange={(e) => setFocusPrompt(e.target.value)}
            disabled={running}
            placeholder="Optional focus (e.g. 'highlight clinical applicability', 'I care about reproducibility')"
            maxLength={1000}
            rows={2}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-[13px] text-slate-700 placeholder-slate-400 resize-none focus:outline-none focus:border-teal-400 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={() => run({ focusPrompt })}
            disabled={running || !llmAvailable || status.status === 'unavailable' || checkingStatus}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-teal-700 text-white text-[13px] font-semibold hover:bg-teal-800 disabled:opacity-50"
            title="Run a condensed full-text digest (what it's about + how to use it + quality)"
          >
            {running && <Spinner size="sm" color="teal-on-fill" />}
            {running
              ? `Analyzing the full text…${
                  status.progress?.eta_seconds != null
                    ? ` (~${formatDuration(status.progress.eta_seconds)} left)`
                    : ''
                }`
              : reviewed ? 'Re-run deeper review' : 'Run deeper review'}
          </button>
      </div>
      )}
      {running && (
        <div className="text-[12px] text-teal-700 space-y-0.5">
          <div>This paper's deep review is running — it appears here when it's done.</div>
          {status.progress?.phase_label && (
            <div className="font-semibold">
              {status.progress.phase_label}
              {status.progress.sub?.total > 0 &&
                ` ${status.progress.sub.done}/${status.progress.sub.total}`}
            </div>
          )}
          {status.progress?.total_elapsed_seconds != null && (
            <div className="text-teal-600">
              {formatDuration(status.progress.total_elapsed_seconds)} elapsed
              {status.progress.eta_seconds != null &&
                ` · ~${formatDuration(status.progress.eta_seconds)} remaining`}
            </div>
          )}
        </div>
      )}
      <ReviewFailure status={status} label="Deep review failed" />
      {online && status.status === 'unavailable' && status.error && (
        <div className="space-y-1 text-[12px] text-amber-800" role="status">
          <div>Review status unavailable: {status.error}. The server job may still be running.</div>
          <button type="button" onClick={retryStatus} disabled={checkingStatus}
            className="font-semibold underline disabled:opacity-50">
            Retry status
          </button>
        </div>
      )}
      {error && <div className="text-[12px] text-rose-700">{error}</div>}
    </div>
  );
}
