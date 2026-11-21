// Thin fetch wrappers for the /api/daily/* endpoints exposed by FastAPI.
// All functions return parsed JSON or throw on non-2xx responses.
// Designed to be called from React Query's `queryFn` / `mutationFn`.

import { fetchJson } from './client.js';

const BASE = '/api';

async function request(path, options = {}) {
  return fetchJson(`${BASE}${path}`, options);
}

/**
 * GET /api/daily
 * Returns the daily slate with up to K papers (Today defaults to K=15; the API
 * accepts 5..20 and scales the model role quota with K).
 * Response shape: { papers, pool_size, lookback_hours,
 *   empty_role_events, awaiting_review_total, showing, fellback_to_recent,
 *   low_relevance_hidden, weak_slate }
 *
 * Cards are ordered by the blended rank (gate score × goal-text similarity ×
 * prestige — same blend as the Library queue), NOT by raw composite_score, so
 * displayed scores may appear out of order by design. The model role hides
 * `dont_read`-band papers; `low_relevance_hidden` counts those left out and
 * `weak_slate` is true when nothing in the pool reached the `should_read` band
 * (the UI shows an honest "light week — trigger a fresh triage" banner).
 *
 * Each paper carries: item_key, item_id, title, authors (string), venue,
 * role, composite_score, surprise_score, corpus_affinity, prestige_score,
 * goal_sim (null = no goal signal), rationale, shap_top, decision,
 * max_author_h_index, feed_name, quality, abstract, pub_year, and `why`
 * (heuristic plain-language reason chips, [] when no signal cleared a
 * threshold).
 */
export async function fetchDailySlate({ K = 5, lookback_hours = 168 } = {}) {
  const qs = new URLSearchParams({
    K: String(K),
    lookback_hours: String(lookback_hours),
  });
  return request(`/daily?${qs.toString()}`);
}

/** Read every retained row, including handled/missing members; never rerank. */
export async function fetchDailyMembers(itemIds, signal) {
  const qs = new URLSearchParams();
  itemIds.forEach(id => qs.append('item_id', String(id)));
  return request(`/daily/members?${qs}`, { signal });
}

/**
 * POST /api/daily/add-to-library
 * Materialize the selected Today cards into the Zotero "Inbox" collection and
 * record a positive training label. `itemIds` are processed_feed_items PKs
 * (SlatePaper.item_id). Returns { added, pending_sync, zotero_sync_error,
 * failed_count, failed, fulltext }. `pending_sync` counts rows whose label
 * saved but the Zotero write itself didn't (writer unavailable / materialize
 * threw) — those items are NOT actually in Zotero yet.
 */
export async function addToLibrary(itemIds, collectionKey = null) {
  if (!Array.isArray(itemIds) || itemIds.length === 0) {
    throw new Error('addToLibrary: itemIds must be a non-empty array');
  }
  return request('/daily/add-to-library', {
    method: 'POST',
    body: JSON.stringify({ item_ids: itemIds, target_collection_key: collectionKey || null }),
  });
}

/**
 * POST /api/daily/trash
 * Record dont_read (strong negative) for the selected cards and mark them
 * read. Returns { trashed, marked_read, marked_read_error, failed_count,
 * failed }. `marked_read_error` is set when the label/decision committed but
 * the best-effort Zotero mark-read failed (e.g. Zotero holds the DB lock).
 */
export async function trashPapers(itemIds) {
  if (!Array.isArray(itemIds) || itemIds.length === 0) {
    throw new Error('trashPapers: itemIds must be a non-empty array');
  }
  return request('/daily/trash', {
    method: 'POST',
    body: JSON.stringify({ item_ids: itemIds }),
  });
}

/**
 * POST /api/daily/events — batch of local exposure events (card_shown, card_dwell,
 * open_review, open_link) for one slate. `keepalive` lets the last batch survive
 * a page unload. Returns { logged }.
 */
export async function postTodayEvents(slateId, events) {
  return request('/daily/events', {
    method: 'POST', keepalive: true, body: JSON.stringify({ slate_id: slateId, events }),
  });
}

/**
 * POST /api/daily/verdict
 * Record ONE card's decision (must_read | should_read | could_read | dont_read)
 * plus optional canonical reason-chip ids. Omit/empty `chips` → reasons_json NULL
 * (a decision with no reason). Reasons are per item — never sent in batch.
 * A positive priority also tries the Zotero Add once per decision (not on a reasons-only re-submit).
 * Returns { id, item_key, reasons_saved, add_attempted, add_status, added_to_library, add_error, zotero_key }.
 */
export async function submitDailyVerdict({ itemId, priority, chips = [] }) {
  if (!Number.isInteger(itemId)) throw new Error('submitDailyVerdict: itemId must be an integer');
  if (!priority) throw new Error('submitDailyVerdict: priority is required');
  return request('/daily/verdict', {
    method: 'POST',
    body: JSON.stringify({ item_id: itemId, user_priority: priority, reasons: chips.length ? chips : null }),
  });
}

/**
 * POST /api/daily/verdict/undo
 * Retract one card's decision (and its reasons), restoring the undecided card.
 * Never deletes a Zotero item. Returns { deleted, item_key, released_pending_add, item_stays_in_zotero, zotero_key }.
 */
export async function undoDailyVerdict(itemId) {
  if (!Number.isInteger(itemId)) throw new Error('undoDailyVerdict: itemId must be an integer');
  return request('/daily/verdict/undo', { method: 'POST', body: JSON.stringify({ item_id: itemId }) });
}

/**
 * POST /api/daily/triage-backlog
 * Start a background drain of the un-triaged feed backlog (scored via the
 * custom remote provider). Returns immediately; poll getTriageStatus().
 */
export async function triggerTriageBacklog() {
  return request('/daily/triage-backlog', { method: 'POST', body: '{}' });
}

/**
 * GET /api/daily/triage-status
 * Poll the backlog-drain job: { running, triaged, gate_rejected, fetched,
 * ticks, error, done, ... }.
 */
export async function getTriageStatus() {
  return request('/daily/triage-status');
}
