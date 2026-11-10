import { request } from './client.js';

const telegram = '/api/research-feed/telegram';
export const telegramFeedApi = {
  sources: () => request(`${telegram}/sources`),
  save: (selection) => request(`${telegram}/sources`, { method: 'PUT', body: JSON.stringify(selection) }),
  status: () => request(`${telegram}/status`),
  today: () => request(`${telegram}/today`),
  reasons: (sourceId, chips) => request(`${telegram}/reasons`, { method: 'POST', body: JSON.stringify({ source_id: sourceId, chips }) }),
  feedback: (sourceId, liked, note) => request(`${telegram}/feedback`, { method: 'POST', body: JSON.stringify({ source_id: sourceId, liked, note }) }),
  feedbackExportUrl: `${telegram}/feedback/export`,
  thread: (sourceId) => request(`${telegram}/thread/${encodeURIComponent(sourceId)}`),
  done: (sourceIds, done) => request(`${telegram}/done`, { method: 'POST', body: JSON.stringify({ source_ids: sourceIds, done }) }),
  history: () => request(`${telegram}/history`),
  historyRun: (runId) => request(`${telegram}/history/${encodeURIComponent(runId)}`),
  processing: (token, allow) => request(`${telegram}/processing`, { method: 'POST', body: JSON.stringify({ token, allow }) }),
  collectConsent: (allow) => request(`${telegram}/collect-consent`, { method: 'POST', body: JSON.stringify({ allow }) }),
  run: () => request(`${telegram}/runs`, { method: 'POST' }),
};

const base = '/api/research-feed/runs';
export const researchFeedApi = {
  list: () => request(`${base}?limit=100`),
  get: (id) => request(`${base}/${encodeURIComponent(id)}`),
  download: (id, format) => `${base}/${encodeURIComponent(id)}/download?format=${encodeURIComponent(format)}`,
};
