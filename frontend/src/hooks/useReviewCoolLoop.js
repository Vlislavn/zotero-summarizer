import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { runReviewFleet, fetchReviewFleetStatus, fetchReadingQueue } from '../api/libraryApi.js';
import { isCoolUndecided, coolUndecidedKeys } from '../utils/relevanceBands.js';

// "Review cool papers": loop the review fleet over EVERY undecided high-relevance
// pick (not a fixed 5) so deep reviews + verdicts stream in without per-paper
// clicking. Extracted from LibraryReadNext into a hook so the orchestration —
// pinning the cool keys, the attempted-ledger dedup, the foreign-prewarm drain,
// and the Stop/stopping settle — is unit-testable in isolation.
//
// Injected deps couple it to the page's queue: `queue` drives the cool count;
// `queueArgs(force)` builds the reading-queue request; `applyQueueData(data)`
// renders a fetched queue (streaming reloads); `loadQueue(force)` is the page's
// full reload; `setMessage`/`setIsError` drive the shared status banner;
// `zoteroReady` gates the mount-resume effect.
const FLEET_CHUNK = 5;              // picks per fleet round (stays inside its batch budget)
const AUTO_REVIEW_MAX_ROUNDS = 12;  // bounds re-fetch of stuck (paywalled) top picks; ~60 papers/session
const AUTO_REVIEW_MAX_DRAINS = 5;   // bound: how many foreign (prewarm) runs to wait out before our keys run

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export function useReviewCoolLoop({ queue, queueArgs, applyQueueData, loadQueue, zoteroReady, setMessage, setIsError }) {
  const [fleetStatus, setFleetStatus] = useState({
    status: 'idle', completed: 0, total: 0, proposed: 0,
    skipped_no_fulltext: 0, failed: 0, error: null, started_at: null, progress: {},
  });
  const fleetPollRef = useRef(null);
  // autoStopRef ends the loop after the in-flight chunk; autoReview drives the bar
  // (active = looping; stopping = Stop pressed, the in-flight chunk is still settling).
  const autoStopRef = useRef(false);
  const [autoReview, setAutoReview] = useState({ active: false, stopping: false });

  // Undecided cool picks (high relevance, no proposal/label) — the bar's count.
  const coolUndecided = useMemo(() => queue.filter(isCoolUndecided).length, [queue]);

  // Lightweight mount-resume poller: reflect an already-running fleet and reload
  // ONCE when it finishes (NOT the streaming auto-loop).
  const pollFleet = useCallback(() => {
    if (fleetPollRef.current) { clearTimeout(fleetPollRef.current); fleetPollRef.current = null; }
    fleetPollRef.current = setTimeout(async () => {
      let s;
      try {
        s = await fetchReviewFleetStatus();
      } catch {
        pollFleet();  // transient — keep waiting, don't false-finish
        return;
      }
      setFleetStatus(s);
      if (s.status === 'running') pollFleet();
      else loadQueue(false);  // done → one reload picks up the proposals
    }, 3000);
  }, [loadQueue]);

  // Await a fleet run to settle, STREAMING the queue each time a paper finishes
  // (cheap cached read) so reviews/verdicts/quality chips appear mid-run instead of
  // only at the end. Returns the terminal status (null if the user hit Stop).
  async function pollFleetUntilDone() {
    let lastCompleted = -1;
    for (;;) {
      if (autoStopRef.current) return null;
      let s;
      try {
        s = await fetchReviewFleetStatus();
      } catch {
        await sleep(3000);  // transient — keep waiting, don't false-finish
        continue;
      }
      setFleetStatus(s);
      if (typeof s.completed === 'number' && s.completed !== lastCompleted) {
        lastCompleted = s.completed;
        try { applyQueueData(await fetchReadingQueue(queueArgs(false))); } catch { /* keep last-good list */ }
      }
      if (s.status !== 'running') return s;
      await sleep(3000);
    }
  }

  // Loop the fleet over EVERY undecided cool pick, FLEET_CHUNK at a time, until the
  // cool set is drained, Stop is pressed, or the max rounds. Each round PINS the next
  // un-attempted cool keys to the fleet (runReviewFleet({itemKeys})) so it reviews the
  // SAME rows the UI counts — not the band-agnostic top-of-undecided slice, which would
  // review higher-blended could_read rows and leave the buried cool stragglers forever.
  // `attempted` dedups vs the keys already dispatched this session, so a slow/errored
  // cool paper is tried at most once and the loop converges instead of re-chewing.
  async function handleReviewCool() {
    if (autoReview.active) return;
    if (fleetPollRef.current) { clearTimeout(fleetPollRef.current); fleetPollRef.current = null; }
    autoStopRef.current = false;
    setAutoReview({ active: true, stopping: false });
    setMessage('');
    setIsError(false);
    const attempted = new Set();
    let drains = 0;
    try {
      for (let round = 0; round < AUTO_REVIEW_MAX_ROUNDS; round += 1) {
        if (autoStopRef.current) break;
        let data;
        try {
          data = await fetchReadingQueue(queueArgs(false));
        } catch (err) {
          setMessage(`Review paused — couldn’t load the queue: ${err.message || err}`);
          setIsError(true);
          break;
        }
        applyQueueData(data);
        const next = coolUndecidedKeys(data.items).filter((k) => !attempted.has(k));
        if (next.length === 0) break;  // every cool paper now attempted or decided → stop
        const chunk = next.slice(0, FLEET_CHUNK);
        const started = await runReviewFleet({ itemKeys: chunk });
        setFleetStatus(started);
        // Single-flight: a prewarm (or a manual deep review) may hold the fleet, so
        // this call did NOT claim our keys (accepted === false) and returned THAT
        // foreign run — its keys, not ours. Drain it (without marking our chunk
        // attempted or counting a round), then re-loop so our own call pins the cool
        // keys once the latch frees. Keys off `accepted`, not a started_at timestamp,
        // so it's robust to a prewarm that fires AFTER the click; `drains` bounds it.
        if (started.accepted === false) {
          if (drains >= AUTO_REVIEW_MAX_DRAINS) break;
          drains += 1;
          setMessage('Finishing a background review already in progress first…');
          setIsError(false);
          const drained = await pollFleetUntilDone();
          if (autoStopRef.current || drained == null) break;
          round -= 1;
          continue;
        }
        chunk.forEach((k) => attempted.add(k));
        const settled = started.status === 'running' ? await pollFleetUntilDone() : started;
        if (autoStopRef.current || settled == null) break;
      }
    } catch (err) {
      setFleetStatus({ status: 'error', completed: 0, total: 0, error: err.message || String(err) });
    } finally {
      // Stop was pressed: the in-flight chunk is still finishing server-side (there's
      // no cancel). Hold the honest "Stopping…" state and poll until it settles — so
      // the bar never shows a stale "Reviewing N of 5" next to a re-enabled button —
      // then fall through to the terminal status.
      if (autoStopRef.current) {
        for (;;) {
          let s;
          try { s = await fetchReviewFleetStatus(); } catch { await sleep(3000); continue; }
          setFleetStatus(s);
          if (s.status !== 'running') break;
          await sleep(3000);
        }
      }
      autoStopRef.current = false;
      setAutoReview({ active: false, stopping: false });
      loadQueue(false);
    }
  }

  function stopReviewCool() {
    autoStopRef.current = true;
    setAutoReview({ active: false, stopping: true });
  }

  // Reflect an already-running fleet on mount so the button doesn't silently
  // no-op (single-flight, one run at a time).
  useEffect(() => {
    if (!zoteroReady) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const s = await fetchReviewFleetStatus();
        if (cancelled) return;
        if (s.status === 'running') { setFleetStatus(s); pollFleet(); }
      } catch {
        /* advisory probe — ignore; the Run path surfaces real errors */
      }
    })();
    return () => {
      cancelled = true;
      autoStopRef.current = true;  // stop any in-flight auto-review loop on unmount
      if (fleetPollRef.current) { clearTimeout(fleetPollRef.current); fleetPollRef.current = null; }
    };
  }, [zoteroReady, pollFleet]);

  return { fleetStatus, autoReview, coolUndecided, handleReviewCool, stopReviewCool };
}
