import { request } from './settingsApi.js';

// Paper-page switches the frontend reads from the server (env-backed, per request).
// `auto_review` mirrors ZS_PAPER_AUTO_REVIEW (default OFF): the paper page only
// auto-starts a deep review on open when this is explicitly on.
export async function fetchPaperPrefs() {
  return request('/api/paper/prefs');
}
