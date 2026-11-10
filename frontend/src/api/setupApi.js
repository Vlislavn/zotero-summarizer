import { request } from './settingsApi.js';

const SETUP_BASE = '/api/setup';

export async function fetchSetupStatus() {
  return request(`${SETUP_BASE}/status`);
}

export async function detectZotero() {
  return request(`${SETUP_BASE}/detect-zotero`);
}

export async function updatePaths(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('updatePaths: body must be an object');
  const pathKeys = { pdf_root: 'PDF_ROOT', zotero_data_dir: 'ZOTERO_DATA_DIR' };
  const keys = Object.keys(body);
  if (keys.some(key => !Object.hasOwn(pathKeys, key))) throw new Error('updatePaths: unsupported path field');
  const expected = keys.map(key => pathKeys[key]).sort();
  const response = await request(`${SETUP_BASE}/paths`, { method: 'PUT', body: JSON.stringify(body) });
  if (response?.restart_required !== true || !Array.isArray(response.written)
    || JSON.stringify([...response.written].sort()) !== JSON.stringify(expected)) {
    throw new Error('Path save not confirmed. The request may already have written paths; no automatic retry or restart was performed.');
  }
  return response;
}

export async function validateSetup({ config, test_connection = false } = {}) {
  if (!config || typeof config !== 'object') {
    throw new Error('validateSetup: config must be an object');
  }
  return request(`${SETUP_BASE}/validate-config`, {
    method: 'POST',
    body: JSON.stringify({ config, test_connection }),
  });
}

export async function fetchAiPresets() {
  return request(`${SETUP_BASE}/ai-presets`);
}

export async function saveAiCredential(name, apiKey, revision) {
  return request(`${SETUP_BASE}/ai-credential`, {
    method: 'PUT',
    body: JSON.stringify({ name, api_key: apiKey, ...(revision ? { revision } : {}) }),
  });
}

export async function fetchDoctorStatus({ signal } = {}) {
  return request(`${SETUP_BASE}/doctor`, { signal });
}

export async function cancelDoctor(runId) {
  return request(`${SETUP_BASE}/doctor/cancel`, {
    method: 'POST', body: JSON.stringify({ run_id: runId }),
  });
}

export async function runDoctor(checkIds = null) {
  return request(`${SETUP_BASE}/doctor`, {
    method: 'POST',
    body: JSON.stringify({ check_ids: checkIds }),
  });
}
