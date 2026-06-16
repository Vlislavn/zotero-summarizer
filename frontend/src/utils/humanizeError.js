// Turn any thrown value into a short, human-readable string for display.
//
// The api/client.js `request()` wrapper throws an Error whose `.message` is
// either the backend's `message`/`detail` field or a synthesized
// `HTTP <status> <statusText>`. Components historically interpolated
// `err.message || String(err)` directly, which (a) leaked the raw `HTTP 503:`
// prefix into the UI and (b) could render `[object Object]` when handed a bare
// object. This centralises one safe, friendly mapping.
//
// Guarantees:
//   - never returns `[object Object]` (handles Error, {message}, string, unknown)
//   - strips a leading `HTTP <digits>:` / `HTTP <digits> <text>:` prefix
//   - maps a known status (from `err.status` or parsed from the message) to a
//     plain-English sentence
//   - falls back to the cleaned message, then a generic line

// status -> friendly sentence. Kept small (the statuses the app actually hits).
const STATUS_MESSAGES = {
  0: 'Cannot reach the server — check that the backend is running.',
  401: 'You are not authorized — check your credentials.',
  403: 'Access to this resource is not allowed.',
  404: 'That resource could not be found.',
  408: 'The request timed out — try again.',
  422: 'This view needs the updated backend — restart the app, then reload.',
  429: 'Too many requests — wait a moment and try again.',
  500: 'The server hit an internal error — try again.',
  502: 'The server is unreachable right now (bad gateway).',
  503: 'The service is unavailable right now — try again shortly.',
  504: 'The server took too long to respond (gateway timeout).',
};

const GENERIC = 'Something went wrong. Please try again.';

// Pull a numeric HTTP status out of a message like "HTTP 503: ...".
function statusFromMessage(message) {
  const m = /^HTTP\s+(\d{3})\b/.exec(message);
  return m ? Number(m[1]) : null;
}

// Drop a leading "HTTP 503:" / "HTTP 503 Service Unavailable:" prefix so the
// remaining detail reads as a sentence, not a transport dump.
function stripHttpPrefix(message) {
  return message.replace(/^HTTP\s+\d{3}\b[^:]*:?\s*/i, '').trim();
}

// Extract a best-effort message string from an unknown thrown value without
// ever producing "[object Object]".
function rawMessage(err) {
  if (err == null) return '';
  if (typeof err === 'string') return err;
  if (err instanceof Error) return err.message || '';
  if (typeof err === 'object') {
    const cand = err.message ?? err.detail ?? err.error;
    if (typeof cand === 'string') return cand;
    if (cand != null && typeof cand !== 'object') return String(cand);
    return '';
  }
  return String(err);
}

/**
 * @param {unknown} err - an Error, a `{message|detail|error}` object, a string,
 *   or anything else. May carry a numeric `.status` (the api client sets it).
 * @returns {string} a short, human-readable message (never "[object Object]").
 */
export function humanizeError(err) {
  const message = rawMessage(err);
  const status =
    (err && typeof err === 'object' && typeof err.status === 'number'
      ? err.status
      : null) ?? statusFromMessage(message);

  // A known status wins, but only when the backend gave no human detail of its
  // own beyond the bare "HTTP <status>" line — otherwise the detail is more
  // specific and we keep it (just stripped of the transport prefix).
  const cleaned = stripHttpPrefix(message);
  if (status != null && STATUS_MESSAGES[status] && !cleaned) {
    return STATUS_MESSAGES[status];
  }
  if (cleaned) return cleaned;
  if (status != null && STATUS_MESSAGES[status]) return STATUS_MESSAGES[status];
  return GENERIC;
}
