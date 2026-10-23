import { request } from './client.js';

const base = '/api/research-feed/runs';
export const researchFeedApi = {
  list: () => request(`${base}?limit=100`),
  get: (id) => request(`${base}/${encodeURIComponent(id)}`),
  download: (id, format) => `${base}/${encodeURIComponent(id)}/download?format=${encodeURIComponent(format)}`,
};
