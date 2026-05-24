// Thin fetch wrappers for the /api/triage/* and /api/calibration/* endpoints.
// Shared request() lives in ./client (extract the rest of api/*.js the same way).

import { request } from './client';

/** GET /api/triage/jobs — recent jobs list. */
export async function fetchJobs() {
  return request('/api/triage/jobs');
}

/** GET /api/triage/jobs/{job_id} — full job detail incl. results & errors. */
export async function fetchJob(jobId) {
  if (!jobId) throw new Error('fetchJob: jobId is required');
  return request(`/api/triage/jobs/${encodeURIComponent(jobId)}`);
}

/** POST /api/triage/jobs/{job_id}/cancel — cancel a running job. */
export async function cancelJob(jobId) {
  if (!jobId) throw new Error('cancelJob: jobId is required');
  return request(`/api/triage/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  });
}

/** GET /api/calibration/metrics — calibration metrics summary. */
export async function fetchCalibrationMetrics() {
  return request('/api/calibration/metrics');
}

/** POST /api/triage/results/{item_key}/feedback — approve/reject result. */
export async function submitResultFeedback(itemKey, verdict) {
  if (!itemKey) throw new Error('submitResultFeedback: itemKey is required');
  if (!['approve', 'reject'].includes(verdict)) {
    throw new Error(`submitResultFeedback: unknown verdict "${verdict}"`);
  }
  return request(`/api/triage/results/${encodeURIComponent(itemKey)}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ verdict }),
  });
}
