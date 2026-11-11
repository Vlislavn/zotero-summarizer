// English wording for the «Check channels» button and the Telegram blocks: age, progress, errors (pure, no I/O).
// Only the channel summaries and trend labels stay in the chats' language.

export function ageText(iso, now = Date.now()) {
  const at = new Date(iso).getTime();
  if (!iso || Number.isNaN(at)) return 'Not checked yet';
  return `Updated ${ago(iso, now)}`;
}

// "just now" / "12 min ago" / "3 h ago" / "2 d ago" / "3 wk ago" — the age of a post or of the last check.
export function ago(iso, now = Date.now()) {
  const at = new Date(iso).getTime();
  if (!iso || Number.isNaN(at)) return '';
  const minutes = Math.max(0, Math.floor((now - at) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h ago`;
  const days = Math.floor(minutes / 1440);
  return days < 14 ? `${days} d ago` : `${Math.floor(days / 7)} wk ago`;
}

// "14:05" for today, "9 Nov, 14:05" otherwise (local time zone) — when a Telegram wait ends or a check happened.
export function when(iso, now = new Date()) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return iso || '';
  const time = date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (date.toDateString() === now.toDateString()) return time;
  return `${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${time}`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
export const postsText = (n = 0) => plural(n, 'post');
export const channelsText = (n = 0) => plural(n, 'channel');
export const commentsText = (n = 0) => plural(n, 'comment');

export function clock(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

const STAGES = { channels: [1, 'Channels'], comments: [2, 'Comments'], summaries: [3, 'Summaries'] };
export const stepOf = (progress) => (STAGES[progress?.stage] || [1])[0];
export function progressText({ progress, elapsed_s: elapsed = 0 }) {
  const [step, name] = STAGES[progress?.stage] || [1, 'Starting'];
  const counts = progress?.total > 0 ? ` ${progress.done}/${progress.total}` : '…';
  return `Step ${step} of 3 · ${name}${counts} · ${clock(elapsed)}`;
}

export const AUTH_CODES = ['missing_session', 'session_unauthorized', 'revoked_session'];
const FAILURES = {
  busy: 'Another process is using the Telegram session (for example a terminal login). Try again in a minute.',
  model_unavailable: 'The local model is not answering. Start it (for example Ollama) and press “Check channels” again.',
  telegram_incomplete: 'The check did not finish: it ran out of time (4.5 min limit) or the network dropped. What was collected is saved — press the button again.',
  interrupted: 'The previous check was interrupted (the app restarted). Press the button again.',
  telegram_action_failed: 'The check failed with an internal error. Try again; if it repeats, see the app log.',
  telegram_sources_required: 'Add at least one channel first: Weekly → Telegram sources.',
  telegram_local_required: 'Pick a summary model in Weekly → Telegram sources (an external provider needs its own permission).',
  telegram_busy: 'Telegram is busy with another action. Try again in a minute.',
};

// `code` is operation.json's `error` or an API error body's `error`.
export function failureText(code, retryAt) {
  if (AUTH_CODES.includes(code)) return 'No access to the Telegram account: the session is missing or expired. Run this once in a terminal:';
  if (code === 'deferred') {
    return `Telegram asks to wait (account rate limit). Try again ${retryAt ? `after ${when(retryAt)}` : 'later'}.`;
  }
  return FAILURES[code] || null;
}

export const partialText = (retryAt) => `Telegram limited the requests: some discussions were not loaded. Try again after ${when(retryAt)}.`;

// The «i» disclosure: what is read, where summaries come from, what the markers mean.
export const SCOPE_TEXT = 'Reads the posts and comments of your selected channels (the last 72 hours of discussions, not the whole history). Summaries are made by a local model on this Mac and are not fact-checked.';
export const CONSENT_TEXT = 'First run: the app will read the posts and comments of your selected channels through your Telegram session — read-only, no joining or sending. Summaries are made by a local model on this Mac. You can revoke this in Telegram sources.';
export const MARKER_LEGEND = [
  ['↗ more discussion', 'more posts on the topic than a week earlier'],
  ['new', 'almost nothing on it a week earlier'],
  ['hot thread', 'a single discussion that blew up'],
];

export const LIKED_CHIPS = [['useful', 'Useful'], ['new_to_me', 'New to me'], ['want_to_try', 'Want to try'], ['good_discussion', 'Good discussion']];

// The short state a collapsed card shows: «not a fit», «marked useful, new to me», «noted».
export function feedbackText(post, reasons) {
  if (reasons) return 'not a fit';
  const labels = Object.fromEntries(LIKED_CHIPS);
  if (post.liked?.length) return `marked ${post.liked.map((id) => labels[id].toLowerCase()).join(', ')}`;
  return post.note ? 'noted' : '';
}
