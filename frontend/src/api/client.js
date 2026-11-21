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
 * Throw the Error for a non-2xx `res` (for the per-module fetch wrappers that do not use `request`):
 * message `HTTP <status>: <message|detail>` from a JSON object body, else
 * `HTTP <status> <statusText>[: <body text>]`; `err.status`; `err.body` = the body as text (JSON re-stringified).
 *
 * @param {Response} res
 * @returns {Promise<never>}
 */
export async function throwHttpError(res) {
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
  const err = /** @type {ApiError} */ (new Error(message));
  err.status = res.status;
  err.body = detail;
  throw err;
}

/**
 * Unwrap a fetched `res` for the per-module wrappers: non-2xx throws via {@link throwHttpError};
 * 204 → null; else `res.json()`.
 *
 * @param {Response} res
 * @returns {Promise<any>}
 */
export async function readJsonOrThrow(res) {
  if (!res.ok) await throwHttpError(res);
  if (res.status === 204) return null;
  return res.json();
}

/**
 * The fetch init for a JSON call: default `Content-Type` merged with `options.headers`,
 * then `...options` (so a caller-supplied `options.headers` replaces the merged headers).
 *
 * @param {RequestInit} [options]
 * @returns {RequestInit}
 */
export function jsonInit(options = {}) {
  return {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  };
}

/**
 * Plain `fetch` of a full `url` with {@link jsonInit}, unwrapped by {@link readJsonOrThrow}.
 *
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<any>}
 */
export async function fetchJson(url, options = {}) {
  return readJsonOrThrow(await fetch(url, jsonInit(options)));
}

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
  return readResponse(await fetchTransport(path, jsonInit(options)));
}

/**
 * Unwrap a fetched `res`: body read once as text, parsed as JSON (else `{ _raw: text }`; empty → null).
 * Non-2xx throws an {@link ApiError}; a failed body read throws an Error with `cause` + `responseReceived`.
 *
 * @param {Response} res
 * @returns {Promise<any>}
 */
export async function readResponse(res) {
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
