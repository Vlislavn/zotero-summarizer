// Shared fetch wrapper for the JSON API. Each api/*.js historically defined its
// own copy of this; new/migrated modules should import `request` from here so
// error handling stays consistent in one tested place.

/**
 * @typedef {Error & { status?: number, body?: unknown }} ApiError
 */

/**
 * Issue a JSON request and unwrap the response.
 * Throws an {@link ApiError} (Error with `status` + parsed `body`) on non-2xx.
 *
 * @param {string} path
 * @param {RequestInit} [options]
 * @returns {Promise<any>}
 */
export async function request(path, options = {}) {
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
    const err = /** @type {ApiError} */ (new Error(message));
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}
