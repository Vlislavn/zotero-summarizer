// Thin fetch wrappers for the /api/config and /api/admin endpoints.
//
// Backend contracts:
//   GET  /api/config                 -> GoalsConfig.model_dump() (plain object)
//   PUT  /api/config                 -> { status: 'ok', config: GoalsConfig }
//   POST /api/admin/refresh-labels   -> { ok, finished_at, total, by_class, ... }
//   POST /api/admin/retrain          -> { job_id, status: 'running' } | 409
//   GET  /api/admin/jobs/{job_id}    -> { status, result, error, progress, ... }
//
// The PUT body is validated against pydantic GoalsConfig — any extra fields
// are silently dropped (pydantic v2 default), so we must mirror the existing
// shape rather than inventing new keys.

const CONFIG_BASE = '/api/config';
const ADMIN_BASE = '/api/admin';

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = '';
    let parsedMessage = '';
    try {
      const body = await res.json();
      if (body && typeof body === 'object') {
        parsedMessage = body.message || body.detail || '';
        detail = JSON.stringify(body);
      }
    } catch {
      try {
        detail = await res.text();
      } catch {
        detail = '';
      }
    }
    const message = parsedMessage
      ? `HTTP ${res.status}: ${parsedMessage}`
      : `HTTP ${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`;
    const err = new Error(message);
    err.status = res.status;
    err.body = detail;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

/**
 * GET /api/config
 * Returns the current GoalsConfig as a plain object.
 */
export async function fetchConfig() {
  return request(CONFIG_BASE);
}

/**
 * PUT /api/config
 * Replaces the runtime config. Returns { status, config }.
 * The full GoalsConfig object must be sent (the backend re-validates strictly).
 */
export async function updateConfig(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('updateConfig: payload must be an object');
  }
  return request(CONFIG_BASE, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

/**
 * POST /api/admin/refresh-labels
 * Re-exports the golden CSV from the live Zotero library. Synchronous;
 * 2-30s depending on Zotero size. Resolves to
 *   { ok, finished_at, total, by_class, by_strength, csv_path, jsonl_path }.
 */
export async function refreshLabels() {
  return request(`${ADMIN_BASE}/refresh-labels`, { method: 'POST' });
}

/**
 * POST /api/admin/retrain
 * Kicks off a classifier retrain in a background thread. Resolves to
 *   { job_id, status: 'running' }
 * or throws on HTTP 409 if another retrain is already running.
 */
export async function retrain({ classifier_name = 'logreg', n_folds = 5 } = {}) {
  return request(`${ADMIN_BASE}/retrain`, {
    method: 'POST',
    body: JSON.stringify({ classifier_name, n_folds }),
  });
}

/**
 * GET /api/admin/jobs/{job_id}
 * Poll the state of a retrain job. Returns
 *   { job_id, kind, status: 'running'|'succeeded'|'failed', started_at,
 *     finished_at, result, error, progress: { done, total } }.
 */
export async function getJob(jobId) {
  if (!jobId) throw new Error('getJob: jobId is required');
  return request(`${ADMIN_BASE}/jobs/${encodeURIComponent(jobId)}`);
}

/**
 * POST /api/admin/llm-check
 * On-demand operational probe of the saved LLM routing config. Probes each of
 * the 3 pipeline stages (feed / backlog / deep_review) with a tiny prompt and
 * reports per-stage pass/fail. A failing stage does NOT fail the request — it
 * comes back with status "fail" and a `detail` string. Resolves to
 *   { status: 'ok'|'degraded',
 *     stages: [ { stage, provider, type, model,
 *                 status: 'operational'|'fail', detail } ] }.
 */
export async function checkLlm() {
  return request(`${ADMIN_BASE}/llm-check`, { method: 'POST' });
}

/**
 * POST /api/admin/llm-models
 * Lists the models a provider serves, for the Settings model-picker. Body is one
 * provider profile ({ name, type, base_url, api_key_env, ... }) taken from the
 * in-progress edit (no "save first" needed). Resolves to
 *   { provider, type, models: string[] }.
 * Throws on a missing API key (400) or an unreachable/erroring endpoint.
 */
export async function listModels(provider) {
  if (!provider || typeof provider !== 'object') {
    throw new Error('listModels: provider must be an object');
  }
  return request(`${ADMIN_BASE}/llm-models`, {
    method: 'POST',
    body: JSON.stringify(provider),
  });
}

/**
 * GET /api/admin/model
 * Returns the freshest-on-disk trained classifier's metadata for the
 * Settings model card. Resolves to ``{model: null}`` when no model has
 * been trained yet, or
 *   { model: { classifier_name, trained_at, git_commit, n_train,
 *              n_positive_library, feature_dim, objective, oof_spearman,
 *              golden_csv_sha256_prefix, thresholds, joblib_path,
 *              joblib_size_bytes, joblib_mtime, runlog } }.
 */
export async function fetchModelCard() {
  return request(`${ADMIN_BASE}/model`);
}
