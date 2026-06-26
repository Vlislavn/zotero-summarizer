// Thin fetch wrappers for the /api/feeds/review/* endpoints.
// Mirrors the Alpine code in zotero_summarizer/web/ui.html (review tab).

const BASE = '/api/feeds/review';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let parsedMessage = '';
    let detail = '';
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
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

/** GET /api/feeds/review?state=...&limit=...&sort=...
 *
 * `sort` is one of:
 *   - `recent` (default): order by created_at DESC.
 *   - `border`: Sprint-3+ active learning — order by distance to
 *     nearest priority threshold (4.5 / 3.6 / 2.6). Most uncertain
 *     rows first so triaging them maximises model lift per click.
 */
export async function fetchReview({ state = 'awaiting_review', limit = 500, sort = 'recent' } = {}) {
  const qs = new URLSearchParams({ state, limit: String(limit), sort });
  return request(`?${qs.toString()}`);
}

/** POST /api/feeds/review/{id}/{action}
 *  action ∈ { 'approve', 'reject', 'relabel' }
 *  For 'relabel' pass `label`. For 'reject' we mirror Alpine and set write_to_golden=true.
 */
export async function reviewAction(id, action, label = null) {
  if (id === undefined || id === null) throw new Error('reviewAction: id is required');
  if (!['approve', 'reject', 'relabel'].includes(action)) {
    throw new Error(`reviewAction: unknown action "${action}"`);
  }
  let body;
  if (action === 'relabel') {
    body = JSON.stringify({ new_priority: label });
  } else if (action === 'reject') {
    body = JSON.stringify({ write_to_golden: true });
  } else {
    body = '{}';
  }
  return request(`/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    body,
  });
}

/** POST /api/feeds/review/apply-all — materialize approved rows into Zotero. */
export async function reviewApplyAll() {
  return request('/apply-all', { method: 'POST', body: '{}' });
}

/** POST /api/feeds/review/confirm-gate-rejected — bulk-confirm unaltered rows as dont_read. */
export async function reviewConfirmAllGateRejected() {
  return request('/confirm-gate-rejected', { method: 'POST', body: '{}' });
}
