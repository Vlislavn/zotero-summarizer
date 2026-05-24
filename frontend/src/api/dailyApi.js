// Thin fetch wrappers for the /api/daily/* endpoints exposed by FastAPI.
// All functions return parsed JSON or throw on non-2xx responses.
// Designed to be called from React Query's `queryFn` / `mutationFn`.

const BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = '';
    let parsedMessage = '';
    try {
      const body = await res.json();
      if (body && typeof body === 'object') {
        parsedMessage = body.message || body.detail || '';
        detail = JSON.stringify(body);
      }
    } catch {
      try {
        detail = await res.text();
      } catch {
        detail = '';
      }
    }
    const message = parsedMessage
      ? `HTTP ${res.status}: ${parsedMessage}`
      : `HTTP ${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`;
    const err = new Error(message);
    err.status = res.status;
    err.body = detail;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

/**
 * GET /api/daily
 * Returns the daily slate with up to K papers.
 * Response shape: { papers, pool_size, capped_at, lookback_hours,
 *   empty_role_events }
 *
 * Each paper carries: item_key, item_id, title, authors (string), venue,
 * role, composite_score, surprise_score, corpus_affinity, prestige_score,
 * rationale, shap_top, decision.
 */
export async function fetchDailySlate({ K = 5, lookback_hours = 168 } = {}) {
  const qs = new URLSearchParams({
    K: String(K),
    lookback_hours: String(lookback_hours),
  });
  return request(`/daily?${qs.toString()}`);
}

/**
 * GET /api/daily/pipeline
 * Funnel overview for Today. Response shape:
 *   { stages: [{ key, label, count, hint, link }], by_decision: {decision: n} }
 * Stages are ordered: in -> filtered -> awaiting -> added -> trashed.
 */
export async function fetchPipeline({ lookback_hours = 168 } = {}) {
  const qs = new URLSearchParams({ lookback_hours: String(lookback_hours) });
  return request(`/daily/pipeline?${qs.toString()}`);
}

/**
 * POST /api/daily/add-to-library
 * Materialize the selected Today cards into the Zotero "Inbox" collection and
 * record a positive training label. `itemIds` are processed_feed_items PKs
 * (SlatePaper.item_id). Returns { added, failed_count, failed }.
 */
export async function addToLibrary(itemIds) {
  if (!Array.isArray(itemIds) || itemIds.length === 0) {
    throw new Error('addToLibrary: itemIds must be a non-empty array');
  }
  return request('/daily/add-to-library', {
    method: 'POST',
    body: JSON.stringify({ item_ids: itemIds }),
  });
}

/**
 * POST /api/daily/trash
 * Record dont_read (strong negative) for the selected cards and mark them
 * read. Returns { trashed, marked_read, failed_count, failed }.
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
 * POST /api/daily/triage-backlog
 * Start a background drain of the un-triaged feed backlog (scored via the
 * custom `sota` provider). Returns immediately; poll getTriageStatus().
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
