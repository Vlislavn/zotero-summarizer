import { useCallback, useEffect, useRef, useState } from 'react';
import { runDeepReview, fetchDeepReviewStatus } from '../api/libraryApi.js';
import { fetchLlmReachability } from '../api/settingsApi.js';

// Run + poll one paper's deep review — the run/poll/reachability/resume logic
// lifted out of DeepReviewSection so the full-page story view can AUTO-RUN it on
// open (the inline card keeps its click-to-run button and does NOT pass autoRun).
//
// `autoRun` fires the review exactly ONCE per itemKey when there is no cached
// review and a PDF exists. The guard is set BEFORE the await (and keyed by
// itemKey) so:
//   - React-StrictMode's double effect invoke can't double-POST (same fiber → the
//     ref persists across the throwaway first pass), and
//   - a completed-but-still-unreviewed result (needs_login / no full text) can't
//     re-fire and loop — the backend's per-item single-flight makes any duplicate
//     a no-op regardless, this just avoids the wasted request.
// The user accepted the Zotero-note side effect of a review when they chose
// auto-generate-on-open, so the note write is left as the review's normal output.
export default function useDeepReviewRunner(itemKey, { deep, onDone, autoRun = false } = {}) {
  const [status, setStatus] = useState({ status: 'idle', completed: 0, total: 0, error: null });
  const [error, setError] = useState(null);
  const [llm, setLlm] = useState(null);
  const [llmChecked, setLlmChecked] = useState(false);
  const pollRef = useRef(null);
  const autoRunFiredFor = useRef(null);

  const reviewed = Boolean(
    deep && !deep.needs_pdf && (deep.digest || deep.quality || (deep.goal_summaries || []).length),
  );

  const poll = useCallback(() => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    pollRef.current = setTimeout(async () => {
      try {
        const s = await fetchDeepReviewStatus(itemKey);
        setStatus(s);
        if (s.status === 'running') poll();
        else onDone?.();
      } catch (e) {
        const msg = e.message || String(e);
        setError(`Could not refresh review status: ${msg}`);
        setStatus((prev) => ({ ...prev, status: 'error', error: msg }));
      }
    }, 3000);
  }, [itemKey, onDone]);

  const run = useCallback(async ({ focusPrompt = '' } = {}) => {
    setError(null);
    try {
      const s = await runDeepReview({ itemKey, focusPrompt });
      setStatus(s);
      if (s.status === 'running') poll();
      else onDone?.();
    } catch (e) {
      setError(`Deep review failed: ${e.message || e}`);
    }
  }, [itemKey, onDone, poll]);

  // Advisory reachability probe so an unreachable model is announced before a run.
  useEffect(() => {
    let cancelled = false;
    setLlmChecked(false);
    (async () => {
      try {
        const h = await fetchLlmReachability();
        if (cancelled) return;
        setLlm((h.stages || []).find((s) => s.stage === 'deep_review') || null);
      } catch {
        /* advisory probe — the run path still surfaces real errors */
      } finally {
        if (!cancelled) setLlmChecked(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Reflect THIS paper's own running review on mount (re-opening mid-run shows
  // live progress). Idempotent GET; no guard needed.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await fetchDeepReviewStatus(itemKey);
        if (cancelled) return;
        if (s.status === 'running') { setStatus(s); poll(); }
      } catch {
        /* The detail query is the primary page load; status resume is opportunistic. */
      }
    })();
    return () => {
      cancelled = true;
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey]);

  // Auto-run once per item when opted in and there's nothing cached to show.
  useEffect(() => {
    if (!autoRun || !itemKey) return;
    if (!llmChecked) return;
    if (reviewed) return;
    if (status.status === 'running') return;
    if (deep && deep.needs_pdf) return;          // honest "no full text" — nothing to run
    if (llm && llm.reachable === false) return;  // model down — the banner explains
    if (autoRunFiredFor.current === itemKey) return;
    autoRunFiredFor.current = itemKey;
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, itemKey, reviewed, deep, llm, llmChecked, status.status]);

  return { status, error, llm, llmChecked, running: status.status === 'running', run };
}
