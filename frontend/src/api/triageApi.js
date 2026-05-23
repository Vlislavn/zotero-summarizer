// Thin fetch wrappers for the /api/triage/* and /api/calibration/* endpoints.
// Mirrors the Alpine code in zotero_summarizer/web/ui.html (triage tab).

async function request(path, options = {}) {
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
    const err = new Error(message);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

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
