import { request } from './client.js';

// Thin fetch wrappers for the /api/pending/* endpoints.

/** GET /api/pending?status=...&limit=... */
export async function fetchPending({ status = 'pending', limit = 500 } = {}) {
  const qs = new URLSearchParams({ status, limit: String(limit) });
  return request(`/api/pending?${qs.toString()}`);
}

/** POST /api/pending/apply with explicit displayed approval hashes. */
export async function applyPending(changeIds, { previews, force = false, retry = false }) {
  if (!previews || typeof previews !== 'object' || Array.isArray(previews) || !Object.keys(previews).length) {
    throw new TypeError('Displayed approval previews are required.');
  }
  return request('/api/pending/apply', {
    method: 'POST',
    body: JSON.stringify({ change_ids: changeIds, previews, force, retry }),
  });
}

/** POST /api/pending/reject { change_ids } */
export async function rejectPending(changeIds) {
  const result = await request('/api/pending/reject', {
    method: 'POST',
    body: JSON.stringify({ change_ids: changeIds }),
  });
  if (!Number.isInteger(result?.updated) || result.updated !== new Set(changeIds).size) {
    throw new Error('Selected rejection was not confirmed completely. Reload the queue to reconcile.');
  }
  return result;
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
