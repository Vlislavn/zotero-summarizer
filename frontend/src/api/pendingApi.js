// Thin fetch wrappers for the /api/pending/* endpoints.
// Mirrors the Alpine code in zotero_summarizer/web/ui.html (pending tab).

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { _raw: text };
    }
  }
  if (!res.ok) {
    const message = (data && (data.message || data.detail))
      || `HTTP ${res.status} ${res.statusText}`;
    const err = new Error(message);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

/** GET /api/pending?status=...&limit=... */
export async function fetchPending({ status = 'pending', limit = 500 } = {}) {
  const qs = new URLSearchParams({ status, limit: String(limit) });
  return request(`/api/pending?${qs.toString()}`);
}

/** POST /api/pending/apply { change_ids, force, retry } */
export async function applyPending(changeIds, { force = false, retry = false } = {}) {
  return request('/api/pending/apply', {
    method: 'POST',
    body: JSON.stringify({ change_ids: changeIds, force, retry }),
  });
}

/** POST /api/pending/reject { change_ids } */
export async function rejectPending(changeIds) {
  return request('/api/pending/reject', {
    method: 'POST',
    body: JSON.stringify({ change_ids: changeIds }),
  });
}

/** PUT /api/pending/change/{id} { payload } */
export async function savePendingChangeEdit(changeId, payload) {
  return request(`/api/pending/change/${encodeURIComponent(changeId)}`, {
    method: 'PUT',
    body: JSON.stringify({ payload }),
  });
}

/** GET /api/zotero/collections — used to build the collection-key dropdown for
 *  collection edits. */
export async function fetchCollections() {
  return request('/api/zotero/collections');
}
