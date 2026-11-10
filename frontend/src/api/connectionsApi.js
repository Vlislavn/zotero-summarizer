import { request } from './settingsApi.js';
const BASE = '/api/llm/connections';
const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) });
function requireReceipt(ok, value) {
  if (!ok) throw new Error('Unconfirmed connection response. Read back the intended effect; do not repeat the write.');
  return value;
}
export async function fetchConnections() {
  const data = await request(BASE);
  return requireReceipt(typeof data?.revision === 'string' && Array.isArray(data.providers), data);
}
export async function registerConnection(command) {
  const data = await post(BASE, command);
  return requireReceipt(data?.operation_id === command.operation_id && data?.provider?.type === command.type
    && (data.provider.base_url || null) === (command.base_url || null)
    && ((command.auth || 'none') === 'none' ? data.provider.auth_mode === 'none' : data.provider.auth_mode !== 'none')
    && typeof data.revision === 'string' && ['draft', 'registered_inactive'].includes(data.state), data);
}
export async function listConnectionModels(id) {
  const data = await post(`${BASE}/${encodeURIComponent(id)}/catalog`, {});
  return requireReceipt(data?.operation_id === id && ['returned', 'unavailable'].includes(data.catalog)
    && Array.isArray(data.models) && data.models.every(x => typeof x === 'string') && typeof data.revision === 'string', data);
}
export async function previewConnection(command) {
  const data = await post(`${BASE}/impact`, command);
  return requireReceipt(data?.revision === command.revision && typeof data.preview === 'string'
    && typeof data.ready_to_apply === 'boolean' && Array.isArray(data.impact?.stages)
    && data.impact.stages.length === 3 && new Set(data.impact.stages.map(row => row.stage)).size === 3
    && data.impact.stages.every(row => ['feed', 'backlog', 'deep_review'].includes(row.stage) && row.after_explicit && typeof row.after?.model === 'string'), data);
}
export async function applyConnection(command, expectedImpact = {}) {
  const data = await post(`${BASE}/apply`, command);
  const routing = data?.config?.llm_routing;
  return requireReceipt(data?.state === 'selection_saved' && data.selection_matches === true
    && typeof data.provider?.name === 'string' && typeof data.revision === 'string'
    && (command.targets || ['default']).every(target => routing?.[target]?.model === command.model && routing[target].provider === data.provider.name)
    && (!expectedImpact.default || ['provider', 'model'].every(key => routing?.default?.[key] === expectedImpact.default.after?.[key]))
    && (expectedImpact.stages || []).every(row => ['provider', 'model'].every(key => routing?.[row.stage]?.[key] === row.after_explicit?.[key])), data);
}
export async function readConnection(id) {
  const data = await request(`${BASE}/${encodeURIComponent(id)}`);
  return requireReceipt(data?.operation_id === id && typeof data.provider_matches === 'boolean'
    && typeof data.selection_matches === 'boolean', data);
}
function validTaskResults(data, stages) {
  if (!Array.isArray(data.stages)) return data.status === 'fail' && data.stages == null;
  return data.stages.length === stages.length && new Set(data.stages.map(row => row.stage)).size === stages.length
    && data.stages.every(row => stages.includes(row.stage)
      && row.provider === data.selections[row.stage].provider && row.model === data.selections[row.stage].model
      && (row.status === 'operational' ? typeof row.response === 'string' && row.response.trim() && row.request_completion?.successful === true
        : ['fail', 'needs_approval', 'disabled'].includes(row.status)))
    && (data.status !== 'ok' || data.stages.every(row => row.status === 'operational'));
}
export async function testConnection(command) {
  const data = await post(`${BASE}/${command.stages ? 'test-tasks' : 'test'}`, command);
  if (command.stages) return requireReceipt(data?.revision === command.revision && typeof data.current === 'boolean'
    && typeof data.attempted === 'boolean' && ['ok', 'degraded', 'disabled', 'fail'].includes(data.status)
    && Array.isArray(data.targets) && JSON.stringify(data.targets) === JSON.stringify(command.stages)
    && command.stages.every(stage => typeof data.selections?.[stage]?.provider === 'string' && typeof data.selections[stage].model === 'string')
    && validTaskResults(data, command.stages), data);
  return requireReceipt(data?.revision === command.revision && data.model === command.model
    && typeof data.attempted === 'boolean' && typeof data.current === 'boolean'
    && ['operational', 'fail', 'needs_approval', 'disabled'].includes(data.status)
    && (data.status !== 'operational' || (typeof data.response === 'string' && data.response.trim() && data.request_completion?.successful === true)), data);
}
export const replaceConnectionCredential = command => post(`${BASE}/credential`, command);
export const readConnectionCredential = token => request(`${BASE}/credential/${encodeURIComponent(token)}`);

export const fetchChecklistEngine = () => request('/api/llm/checklist-engine');
export const testChecklistEngine = command => post('/api/llm/checklist-engine/test', command);
