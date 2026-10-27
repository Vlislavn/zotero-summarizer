import { request } from './client.js';

const telegram = '/api/research-feed/telegram';
export const telegramFeedApi = {
  sources: () => request(`${telegram}/sources`),
  save: (selection) => request(`${telegram}/sources`, { method: 'PUT', body: JSON.stringify(selection) }),
  status: () => request(`${telegram}/status`),
  run: (consent) => request(`${telegram}/runs`, { method: 'POST', body: JSON.stringify(consent) }),
};

const base = '/api/research-feed/runs';
export const researchFeedApi = {
  list: () => request(`${base}?limit=100`),
  get: (id) => request(`${base}/${encodeURIComponent(id)}`),
  download: (id, format) => `${base}/${encodeURIComponent(id)}/download?format=${encodeURIComponent(format)}`,
};
