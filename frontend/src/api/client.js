// Shared fetch wrapper for the JSON API. Each api/*.js historically defined its
// own copy of this; new/migrated modules should import `request` from here so
// error handling stays consistent in one tested place.

import { fetchTransport } from './offlineTransport.js';

/**
 * @typedef {Error & { status?: number, body?: unknown, responseReceived?: boolean }} ApiError
 */

// The server answers 503 `database_busy` when a writer outlasted sqlite's lock wait: transient.
const BUSY_RETRY_DELAYS_MS = [500, 1500];

/**
 * Issue a JSON request and unwrap the response.
 * Throws an {@link ApiError} (Error with `status` + parsed `body`) on non-2xx.
 * A 503 `database_busy` is retried quietly (≤2 more tries, backoff) for GETs and for calls
 * the caller marks `idempotent: true` (replay-safe writes); then it surfaces as usual.
 *
 * @param {string} path
 * @param {RequestInit & { idempotent?: boolean }} [options]
 * @returns {Promise<any>}
 */
export async function request(path, options = {}) {
  const { idempotent, ...init } = options;
  const replaySafe = idempotent ?? (init.method || 'GET').toUpperCase() === 'GET';
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await requestOnce(path, init);
    } catch (err) {
      const delay = BUSY_RETRY_DELAYS_MS[attempt];
      if (!replaySafe || err.status !== 503 || err.body?.error !== 'database_busy'
          || delay === undefined || init.signal?.aborted) throw err;
      await new Promise((resolve) => { setTimeout(resolve, delay); });
    }
  }
}

async function requestOnce(path, options) {
  const res = await fetchTransport(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let text;
  try {
    text = await res.text();
  } catch (cause) {
    const error = new Error(cause?.message || String(cause));
    error.cause = cause;
    error.responseReceived = true;
    if (!res.ok) error.status = res.status;
    throw error;
  }
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
    const err = /** @type {ApiError} */ (new Error(message));
    err.status = res.status;
    err.body = data;
    err.responseReceived = true;
    throw err;
  }
  return data;
}
