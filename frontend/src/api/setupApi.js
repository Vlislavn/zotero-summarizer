// Thin fetch wrappers for the first-run setup / readiness endpoints.
//
// Reuses settingsApi's `request()` (shared error parsing: a 400's `.message`
// becomes the thrown Error's message). FROZEN BACKEND CONTRACT — consume the
// exact shapes below, never invent field names.
//
//   GET  /api/setup/status         -> {
//     ready, config:{present,valid,research_goals_count,error},
//     llm:{default_provider,default_model,api_key_env,api_key_present,reachable,detail},
//     paths:{pdf_root:{value,set,exists}, zotero_data_dir:{value,set,exists}},
//     zotero:{db_found,data_dir,db_path,library_item_count,feed_count,error},
//     classifier:{trained,classifier_name,trained_at} }
//   GET  /api/setup/detect-zotero  -> { candidates: [{data_dir,db_path,db_exists,storage_exists,source}] }
//   PUT  /api/setup/paths          -> { written:[str], restart_required:true,
//                                       validated:{pdf_root_exists,zotero_db_found} }
//   POST /api/setup/validate-config-> { valid, field_errors:[{loc,msg}], connection:{...}|null }

import { request } from './settingsApi.js';

const SETUP_BASE = '/api/setup';

/**
 * GET /api/setup/status
 * The single readiness payload everything derives from.
 */
export async function fetchSetupStatus() {
  return request(`${SETUP_BASE}/status`);
}

/**
 * GET /api/setup/detect-zotero
 * Auto-detected Zotero data-dir candidates (no side effects).
 */
export async function detectZotero() {
  return request(`${SETUP_BASE}/detect-zotero`);
}

/**
 * PUT /api/setup/paths
 * Writes pdf_root and/or zotero_data_dir. Returns which paths were written and
 * whether a restart is required (always true) plus a quick existence validation.
 * Pass only the keys you want to change.
 */
export async function updatePaths(body) {
  if (!body || typeof body !== 'object') {
    throw new Error('updatePaths: body must be an object');
  }
  return request(`${SETUP_BASE}/paths`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

/**
 * POST /api/setup/validate-config
 * Validates a candidate GoalsConfig (field-level errors) and, when
 * `test_connection` is true, also probes the configured LLM. Returns
 *   { valid, field_errors: [{loc, msg}], connection: {...}|null }.
 */
export async function validateSetup({ config, test_connection = false } = {}) {
  if (!config || typeof config !== 'object') {
    throw new Error('validateSetup: config must be an object');
  }
  return request(`${SETUP_BASE}/validate-config`, {
    method: 'POST',
    body: JSON.stringify({ config, test_connection }),
  });
}
