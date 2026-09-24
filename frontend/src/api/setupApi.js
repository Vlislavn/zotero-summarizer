import { request } from './settingsApi.js';

const SETUP_BASE = '/api/setup';

export async function fetchSetupStatus() {
  return request(`${SETUP_BASE}/status`);
}

export async function detectZotero() {
  return request(`${SETUP_BASE}/detect-zotero`);
}

export async function updatePaths(body) {
  if (!body || typeof body !== 'object') {
    throw new Error('updatePaths: body must be an object');
  }
  return request(`${SETUP_BASE}/paths`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
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

export async function saveAiCredential(name, apiKey) {
  return request(`${SETUP_BASE}/ai-credential`, {
    method: 'PUT',
    body: JSON.stringify({ name, api_key: apiKey }),
  });
}

export async function fetchDoctorStatus() {
  return request(`${SETUP_BASE}/doctor`);
}

export async function runDoctor(checkIds = null) {
  return request(`${SETUP_BASE}/doctor`, {
    method: 'POST',
    body: JSON.stringify({ check_ids: checkIds }),
  });
}
