import { request } from './client.js';

const base = '/api/research-feed';
export const researchFeedApi = {
  list: () => request(`${base}/runs`),
  get: (id) => request(`${base}/runs/${encodeURIComponent(id)}`),
  run: (body) => request(`${base}/run`, { method: 'POST', body: JSON.stringify(body) }),
  download: (id, format) => `${base}/runs/${encodeURIComponent(id)}/download?format=${format}`,
};
