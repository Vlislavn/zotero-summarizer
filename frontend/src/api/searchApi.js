// Thin fetch wrappers for the /api/search/* endpoints (Targeted Search).
// SCREEN is fast (topic -> ranked candidates); REVIEW runs in the background and
// the caller polls getSession until status is 'reviewed' (or 'error').
import { request } from './client.js';

/** POST /api/search/screen — topic -> ranked, deduped candidates (a ResearchSession). */
export async function screen({ query, questions = [] }) {
  return request('/api/search/screen', {
    method: 'POST',
    body: JSON.stringify({ query, questions }),
  });
}

/** POST /api/search/{id}/review — kick off the light+deep review in the background. */
export async function startReview(sessionId) {
  return request(`/api/search/${encodeURIComponent(sessionId)}/review`, {
    method: 'POST',
    body: '{}',
  });
}

/** GET /api/search/{id} — poll one session (status + candidates + reviews). */
export async function getSession(sessionId) {
  return request(`/api/search/${encodeURIComponent(sessionId)}`);
}

/** POST /api/search/{id}/materialize — file one candidate into a chosen Zotero
 * collection (collectionKey falsy → Inbox). Returns { status, zotero_key, collection }. */
export async function materialize(sessionId, candidateId, collectionKey) {
  return request(`/api/search/${encodeURIComponent(sessionId)}/materialize`, {
    method: 'POST',
    body: JSON.stringify({ candidate_id: candidateId, collection_key: collectionKey || null }),
  });
}

/** GET /api/search — newest-first saved-session summaries for the sidebar. */
export async function listSessions() {
  return request('/api/search');
}

/** DELETE /api/search/{id}. */
export async function deleteSession(sessionId) {
  return request(`/api/search/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
}
