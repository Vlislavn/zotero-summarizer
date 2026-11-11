import { request } from './client.js';

const BASE = '/api/feedback-analysis';
export const feedbackAnalysisApi = {
  status: () => request(`${BASE}/status`),
  latest: () => request(`${BASE}/latest`),
  run: () => request(`${BASE}/run`, { method: 'POST' }),
  setWeekly: (enabled) => request(`${BASE}/weekly`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
};
