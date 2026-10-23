// Thin fetch wrappers for the /api/golden/* endpoints exposed by FastAPI.
// All functions return parsed JSON or throw on non-2xx responses.
// Designed to be called from React Query's `queryFn` / `mutationFn`.

import { cacheReviewDetail, cachedReviewDetail, queueMutation, transportUnavailable } from '../offlineStore.js';
import { offlineRequest } from './offlineTransport.js';

function requestSync() {
  window.dispatchEvent(new Event('zs-sync-request'));
}

const BASE = '/api/golden';

async function request(path, options = {}, localFallback = false) {
  const init = { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options };
  return localFallback ? offlineRequest(`${BASE}${path}`, init, readResponse)
    : readResponse(await fetch(`${BASE}${path}`, init));
}

async function readResponse(res) {
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
 * GET /api/golden/provenance/list
 * Returns { items, total_matched, total_rows, flag_counts }.
 */
export async function fetchProvenanceList({ priority, flag, limit = 200, collection, tag, search } = {}) {
  const qs = new URLSearchParams();
  if (priority) qs.set('priority', priority);
  if (flag) qs.set('flag', flag);
  if (collection) qs.set('collection', collection);
  if (tag) qs.set('tag', tag);
  if (search && search.trim()) qs.set('search', search.trim());
  if (limit) qs.set('limit', String(limit));
  const path = qs.toString() ? `/provenance/list?${qs.toString()}` : '/provenance/list';
  return request(path);
}

/**
 * GET /api/golden/review-detail?item_key=...
 * Returns the full per-item detail payload.
 */
export async function fetchReviewDetail(itemKey) {
  if (!itemKey) throw new Error('fetchReviewDetail: itemKey is required');
  const qs = new URLSearchParams({ item_key: itemKey });
  if (transportUnavailable()) {
    const cached = await cachedReviewDetail(itemKey);
    if (cached) return cached;
  }
  try {
    const data = await request(`/review-detail?${qs.toString()}`, {}, true);
    return cacheReviewDetail(itemKey, data);
  } catch (err) {
    if (!err.transportFailure) throw err;
    const cached = await cachedReviewDetail(itemKey);
    if (cached) return cached;
    throw err;
  }
}

/**
 * POST /api/golden/verdict
 * Body: { item_key, user_priority, comment }
 * Returns: { id, created_at, ... }.
 */
export async function submitVerdict({ item_key, user_priority, comment }) {
  if (!item_key) throw new Error('submitVerdict: item_key is required');
  if (!user_priority) throw new Error('submitVerdict: user_priority is required');
  const mutation = { item_key, field: 'verdict', value: user_priority, comment: comment ?? '' };
  if (transportUnavailable()) return queueMutation(mutation);
  try {
    const result = await request('/verdict', {
      method: 'POST', body: JSON.stringify({ item_key, user_priority, comment: comment ?? '' }),
    }, true);
    requestSync();
    return result;
  } catch (err) {
    if (!err.transportFailure) throw err;
    return queueMutation(mutation);
  }
}

/**
 * POST /api/golden/review-note
 * Body: { item_key, note }
 * Returns: { saved: true, note_written: boolean, note_error: string|null }.
 * Local save always succeeds; note_written reflects the best-effort Zotero mirror
 * (false + note_error when Zotero is open), exactly like the verdict-note path.
 */
export async function saveReviewNote({ item_key, note }) {
  if (!item_key) throw new Error('saveReviewNote: item_key is required');
  const mutation = { item_key, field: 'review_note', value: note ?? '' };
  if (transportUnavailable()) return queueMutation(mutation);
  try {
    const result = await request('/review-note', {
      method: 'POST', body: JSON.stringify({ item_key, note: note ?? '' }),
    }, true);
    requestSync();
    return result;
  } catch (err) {
    if (!err.transportFailure) throw err;
    return queueMutation(mutation);
  }
}

/**
 * DELETE /api/golden/verdict?item_key=...
 * Returns: { deleted: boolean }.
 */
export async function deleteVerdict(itemKey) {
  if (!itemKey) throw new Error('deleteVerdict: itemKey is required');
  const mutation = { item_key: itemKey, field: 'verdict', operation: 'delete' };
  if (transportUnavailable()) return queueMutation(mutation);
  const qs = new URLSearchParams({ item_key: itemKey });
  try {
    const result = await request(`/verdict?${qs.toString()}`, { method: 'DELETE' }, true);
    requestSync();
    return result;
  } catch (err) {
    if (!err.transportFailure) throw err;
    return queueMutation(mutation);
  }
}

/**
 * GET /api/golden/effective-labels/summary
 * Returns: { total_rows, user_verdicts, user_confirmed_derivation,
 *   user_overrode_derivation, machine_provisional, outcome_corrected }.
 * Aggregate counts for the hybrid ground-truth pipeline — powers the
 * "Effective labels" strip and the "Used as GT" badge tooltip context.
 * `user_verdicts` counts DELIBERATE verdicts only; provisional "Add to
 * library" verdicts are reported separately (`machine_provisional`, plus
 * the subset already corrected by a 7-day outcome, `outcome_corrected`).
 */
export async function fetchEffectiveLabelsSummary() {
  return request('/effective-labels/summary');
}

/**
 * GET /api/golden/effective-labels
 * Returns: { items: [{ item_key, derived_priority, user_priority,
 *                      effective_priority, source, comment }], total }.
 * The full hybrid ground-truth map. `source` is `'user'` when the row
 * carries a user verdict (i.e. flows into model retraining as GT),
 * otherwise `'derived'`.
 */
export async function fetchEffectiveLabels() {
  return request('/effective-labels');
}

/**
 * GET /api/golden/border-suggestions?top_k=20
 * Returns library rows whose predicted score sits closest to a class
 * boundary — re-labelling these gives the highest marginal AUC lift per
 * label. Items already carry a `current_priority` (derived) and a
 * `predicted_priority` from the model. `disagrees=true` flags rows where
 * model and derivation conflict.
 *
 * Backend re-trains the LightGBM regressor on every call (~30 s), so the
 * caller should cache the result and refetch on demand only.
 */
export async function fetchBorderSuggestions({ topK = 20 } = {}) {
  const qs = new URLSearchParams({ top_k: String(topK) });
  return request(`/border-suggestions?${qs.toString()}`);
}
