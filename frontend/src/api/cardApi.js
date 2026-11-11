// Thin fetch wrappers for the Today/paper card service (Todo #98).
//
//   GET  /api/card/{key}          -> cached AI card OR facts-only (ai_available:false + reason)
//   POST /api/card/{key}/generate -> generate one paper's card, consent-gated + idempotent
//
// `key` is the paper's stable/feed key. A `consentToken` (taken from the GET's
// `boundary.token`) records durable consent for the current card model.

import { request } from './client.js';

export async function fetchCard(key) {
  return request(`/api/card/${encodeURIComponent(key)}`);
}

export async function generateCard(key, consentToken = null) {
  return request(`/api/card/${encodeURIComponent(key)}/generate`, {
    method: 'POST',
    body: JSON.stringify({ consent_token: consentToken }),
  });
}
