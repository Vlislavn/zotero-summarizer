// Best-effort, local-only record of what the owner sees and opens on Today
// (card shown, dwell, link opens). Events are batched per slate; a failed or
// refused send is dropped — telemetry must never disturb reading or deciding.
import { postTodayEvents } from '../api/dailyApi.js';

const FLUSH_MS = 5000;
const MAX_BATCH = 50;
let queue = [];
let timer = null;
let reviewContext = null; // { slateId, itemKey } while a paper opened from Today is on screen

export function flushTodayEvents() {
  clearTimeout(timer);
  timer = null;
  const bySlate = new Map();
  for (const { slateId, event } of queue) bySlate.set(slateId, [...(bySlate.get(slateId) || []), event]);
  queue = [];
  for (const [slateId, events] of bySlate) {
    for (let start = 0; start < events.length; start += MAX_BATCH) {
      postTodayEvents(slateId, events.slice(start, start + MAX_BATCH)).catch(() => {});
    }
  }
}

export function trackToday(slateId, event) {
  if (!slateId || !event?.item_key) return;
  queue.push({ slateId, event: {
    item_id: null, surface: 'today', target: null, ms: null, ...event, client_ts: new Date().toISOString(),
  } });
  if (queue.length >= MAX_BATCH) flushTodayEvents();
  else if (!timer) timer = setTimeout(flushTodayEvents, FLUSH_MS);
}

export function setTodayReviewContext(context) {
  reviewContext = context;
}

// Link opened on the review page of a paper reached from Today (no-op elsewhere).
export function trackTodayLink(target) {
  if (reviewContext) trackToday(reviewContext.slateId, {
    item_key: reviewContext.itemKey, kind: 'open_link', surface: 'review', target,
  });
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushTodayEvents();
  });
  window.addEventListener('pagehide', flushTodayEvents);
}
