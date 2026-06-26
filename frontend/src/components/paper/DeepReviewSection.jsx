import { useEffect, useRef, useState } from 'react';
import { runDeepReview, fetchDeepReviewStatus } from '../../api/libraryApi.js';
import { fetchLlmReachability } from '../../api/settingsApi.js';
import Spinner from '../ui/Spinner.jsx';
import PaperReview from './review/PaperReview.jsx';

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
  const [status, setStatus] = useState({ status: 'idle', completed: 0, total: 0, error: null });
  const [error, setError] = useState(null);
  const [focusPrompt, setFocusPrompt] = useState('');
  // Reachability of the deep_review LLM endpoint (null = unknown/probing).
  const [llm, setLlm] = useState(null);
  const pollRef = useRef(null);

  // Proactively probe the deep_review endpoint so an unreachable model is
  // announced BEFORE the user clicks Run — the failure that silently produced an
  // empty brief. Cheap GET /models; a probe error is advisory and just hides the
  // banner (never blocks the section).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await fetchLlmReachability();
        if (cancelled) return;
        setLlm((h.stages || []).find((s) => s.stage === 'deep_review') || null);
      } catch {
        /* advisory probe — ignore, the run path still surfaces real errors */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // On mount, reflect THIS paper's own running review (if any) so re-opening it mid-run
  // shows its live progress. Reviews are per-paper now, so another paper running never
  // shows up here (the old global-single-flight bug that rendered the wrong paper).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const s = await fetchDeepReviewStatus(itemKey);
      if (cancelled) return;
      if (s.status === 'running') { setStatus(s); poll(); }
    })();
    return () => {
      cancelled = true;
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey]);

  function poll() {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    pollRef.current = setTimeout(async () => {
      const s = await fetchDeepReviewStatus(itemKey);
      setStatus(s);
      if (s.status === 'running') poll();
      else onDone?.();
    }, 3000);
  }

  async function handleRun() {
    setError(null);
    try {
      const s = await runDeepReview({ itemKey, focusPrompt });
      setStatus(s);
      if (s.status === 'running') poll();
      else onDone?.();
    } catch (e) {
      setError(`Deep review failed: ${e.message || e}`);
    }
  }

  const running = status.status === 'running';
  const reviewed = deep && !deep.needs_pdf && (deep.digest || deep.quality || (deep.goal_summaries || []).length);
  return (
    <div className="space-y-3">
      {llm && llm.reachable === false && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] leading-relaxed text-rose-800" role="alert">
          <span className="font-semibold">Deep-review model unreachable.</span>{' '}
          <span className="font-mono text-[12px]">{llm.model || '(model unset)'}</span> at{' '}
          <span className="font-mono text-[12px]">{llm.base_url || '(no base URL)'}</span> isn’t responding,
          so a review will produce no digest. Start that server, or pick a reachable model in{' '}
          <span className="font-semibold">Settings → LLM routing</span>.
          {llm.detail && <div className="mt-1 text-[11px] text-rose-600 break-words">{llm.detail}</div>}
        </div>
      )}
      {deep && deep.needs_pdf && deep.needs_login && deep.login_url && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] leading-relaxed text-amber-800">
          <span className="font-semibold">Needs your library sign-in.</span>{' '}
          The full text is behind your institution’s access.{' '}
          <a href={deep.login_url} target="_blank" rel="noopener noreferrer" className="text-indigo-700 font-medium hover:underline">
            Open it in your browser
          </a>{' '}
          to sign in, then re-run.
        </div>
      )}
      {deep && deep.needs_pdf && !(deep.needs_login && deep.login_url) && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] leading-relaxed text-amber-800">
          <span className="font-semibold">No full text available.</span>{' '}
          The review tried open access, PubMed Central, and your library session
          but couldn’t reach a readable copy.
        </div>
      )}

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
            onClick={handleRun}
            disabled={running || llm?.reachable === false}
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
      {status.status === 'error' && status.error && (
        <div className="text-[12px] text-rose-700">Deep review failed: {status.error}</div>
      )}
      {error && <div className="text-[12px] text-rose-700">{error}</div>}
    </div>
  );
}
