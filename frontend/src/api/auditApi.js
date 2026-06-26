// Thin fetch wrappers for the /api/relabel-audit/* endpoints.
// Mirrors the Alpine code in zotero_summarizer/web/ui.html (audit tab).
// All functions return parsed JSON or throw on non-2xx responses.

const BASE = '/api/relabel-audit';

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

/** POST /api/relabel-audit/init — start/resume an audit session. */
export async function auditInit({ sample_size = 100, seed = 42, resume_if_exists = true } = {}) {
  return request('/init', {
    method: 'POST',
    body: JSON.stringify({ sample_size, seed, resume_if_exists }),
  });
}

/** GET /api/relabel-audit/next — next blind candidate. */
export async function auditNext() {
  return request('/next');
}

/** POST /api/relabel-audit/{item_key} — submit a re-label for the current candidate. */
export async function auditSubmit(itemKey, newPriority) {
  if (!itemKey) throw new Error('auditSubmit: itemKey is required');
  return request(`/${encodeURIComponent(itemKey)}`, {
    method: 'POST',
    body: JSON.stringify({ new_priority: newPriority }),
  });
}

/** GET /api/relabel-audit/metrics — reliability metrics for the session. */
export async function auditMetrics() {
  return request('/metrics');
}

/** POST /api/relabel-audit/reset — wipe the session and start over. */
export async function auditReset() {
  return request('/reset', { method: 'POST' });
}
