import { afterEach, expect, it, vi } from 'vitest';
import { registerConnection, applyConnection, listConnectionModels, testConnection } from './connectionsApi.js';
afterEach(() => vi.unstubAllGlobals());
const command = { operation_id: 'synthetic-uuid', type: 'openai', base_url: 'https://example.invalid/proxy', model: 'manual', revision: 'r' };
function respond(body) { vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })); }
it.each([null, {}, { state: 'registered_inactive' }, { operation_id: 'foreign', provider: {} }])('refuses malformed registration ACK %j without replay', async body => {
  respond(body);
  await expect(registerConnection(command)).rejects.toThrow(/Unconfirmed/);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it.each([null, {}, { state: 'selection_saved', selection_matches: true, config: { llm_routing: { default: { model: 'wrong' } } } }])('refuses malformed or foreign selection ACK %j', async body => {
  respond(body);
  await expect(applyConnection(command)).rejects.toThrow(/Unconfirmed/);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it.each([['none', 'legacy'], ['new', 'none']])('rejects changed auth ownership %s -> %s in a registration ACK', async (auth, returned) => {
  respond({ operation_id: command.operation_id, revision: 'r', state: 'registered_inactive',
    provider: { type: command.type, base_url: command.base_url, auth_mode: returned } });
  await expect(registerConnection({ ...command, auth })).rejects.toThrow(/Unconfirmed/);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('a successful-looking test without owned terminal evidence is not accepted', async () => {
  respond({ revision: 'r', model: 'manual', attempted: true, current: true, status: 'operational', response: 'text alone' });
  await expect(testConnection(command)).rejects.toThrow(/Unconfirmed/);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it.each([undefined, 'true'])('refuses a successful test with non-boolean current scope %j', async current => {
  respond({ revision: 'r', model: 'manual', attempted: true, status: 'operational', response: 'Synthetic response',
    current, request_completion: { successful: true } });
  await expect(testConnection(command)).rejects.toThrow(/Unconfirmed/);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('retains an explicitly stale owned observation without claiming current scope', async () => {
  respond({ revision: 'r', model: 'manual', attempted: true, status: 'operational', response: 'Synthetic response',
    current: false, request_completion: { successful: true } });
  expect((await testConnection(command)).current).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('accepts a task-only ACK only when default and every previewed route match', async () => {
  const before = { provider: 'old', model: 'fallback' };
  const after = { provider: 'chosen', model: 'manual' };
  const impact = { default: { after: before }, stages: [{ stage: 'deep_review', after_explicit: after }] };
  const ack = { state: 'selection_saved', selection_matches: true, revision: 'next', provider: { name: 'chosen' },
    config: { llm_routing: { default: before, deep_review: after } } };
  respond(ack);
  expect((await applyConnection({ ...command, targets: ['deep_review'] }, impact)).config.llm_routing.default).toEqual(before);
  respond({ ...ack, config: { llm_routing: { default: after, deep_review: after } } });
  await expect(applyConnection({ ...command, targets: ['deep_review'] }, impact)).rejects.toThrow(/Unconfirmed/);
});
it.each(['ok', 'degraded'])('task checks reject successful-looking foreign model evidence in %s receipts', async status => {
  const cmd = { revision: 'r', stages: ['deep_review'] };
  const result = { revision: 'r', current: true, attempted: true, status, targets: ['deep_review'],
    selections: { deep_review: { provider: 'selected', model: 'manual' } },
    stages: [{ stage: 'deep_review', provider: 'selected', model: 'foreign', status: 'operational', response: 'Synthetic reply', request_completion: { successful: true } }] };
  respond(result);
  await expect(testConnection(cmd)).rejects.toThrow(/Unconfirmed/);
  expect(fetch.mock.calls[0][0]).toBe('/api/llm/connections/test-tasks');
  respond({ ...result, stages: [{ ...result.stages[0], model: 'manual' }] });
  expect((await testConnection(cmd)).status).toBe(status);
});
it('an empty returned catalog is truthful metadata, not a selection acknowledgement', async () => {
  respond({ operation_id: command.operation_id, revision: 'r', models: [], catalog: 'returned' });
  expect((await listConnectionModels(command.operation_id)).models).toEqual([]);
  expect(fetch).toHaveBeenCalledTimes(1);
});
