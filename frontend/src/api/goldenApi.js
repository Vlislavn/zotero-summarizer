// Thin fetch wrappers for the /api/golden/* endpoints exposed by FastAPI.
// All functions return parsed JSON or throw on non-2xx responses.
// Designed to be called from React Query's `queryFn` / `mutationFn`.

const BASE = '/api/golden';

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
 * GET /api/golden/provenance/list
 * Returns { items, total_matched, total_rows, flag_counts }.
 */
export async function fetchProvenanceList({ priority, flag, limit = 200 } = {}) {
  const qs = new URLSearchParams();
  if (priority) qs.set('priority', priority);
  if (flag) qs.set('flag', flag);
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
  return request(`/review-detail?${qs.toString()}`);
}

/**
 * POST /api/golden/verdict
 * Body: { item_key, user_priority, comment }
 * Returns: { id, created_at, ... }.
 */
export async function submitVerdict({ item_key, user_priority, comment }) {
  if (!item_key) throw new Error('submitVerdict: item_key is required');
  if (!user_priority) throw new Error('submitVerdict: user_priority is required');
  return request('/verdict', {
    method: 'POST',
    body: JSON.stringify({ item_key, user_priority, comment: comment ?? '' }),
  });
}

/**
 * DELETE /api/golden/verdict?item_key=...
 * Returns: { deleted: boolean }.
 */
export async function deleteVerdict(itemKey) {
  if (!itemKey) throw new Error('deleteVerdict: itemKey is required');
  const qs = new URLSearchParams({ item_key: itemKey });
  return request(`/verdict?${qs.toString()}`, { method: 'DELETE' });
}
