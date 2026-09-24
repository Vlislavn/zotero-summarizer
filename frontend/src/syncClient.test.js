// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  request: vi.fn(), applyPull: vi.fn(), applyPushResults: vi.fn(),
  getMeta: vi.fn(), pendingMutations: vi.fn(), publishStatus: vi.fn(),
}));

vi.mock('./api/client.js', () => ({ request: mocks.request }));
vi.mock('./offlineStore.js', () => ({
  applyPull: mocks.applyPull, applyPushResults: mocks.applyPushResults,
  getMeta: mocks.getMeta, pendingMutations: mocks.pendingMutations,
  publishStatus: mocks.publishStatus,
  mutationError: () => null, pushPredecessors: async () => [],
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.pendingMutations.mockResolvedValue([]);
  mocks.getMeta.mockResolvedValue(0);
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});

it('refreshes an open review after a rejected offline verdict is reconciled', async () => {
  mocks.pendingMutations.mockResolvedValue([{ mutation_id: 'v1', item_key: 'feed:g:paper',
    field: 'verdict', operation: 'set', value: 'must_read', status: 'pending' }]);
  mocks.request.mockImplementation(async (path) => path === '/api/sync/push'
    ? { protocol: 1, results: [{ mutation_id: 'v1', status: 'rejected', error: 'review_required' }] }
    : { protocol: 1, cursor: 1, papers: [{ item_key: 'feed:g:paper', verdict: null }] });
  const client = { invalidateQueries: vi.fn().mockResolvedValue() };

  await (await import('./syncClient.js')).syncNow(client);

  expect(mocks.applyPushResults).toHaveBeenCalledWith([
    { mutation_id: 'v1', status: 'rejected', error: 'review_required' },
  ]);
  expect(mocks.applyPull).toHaveBeenCalledOnce();
  expect(client.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['review-detail'] });
});

it('surfaces a protocol mismatch instead of masking it as an outage', async () => {
  mocks.request.mockResolvedValue({ protocol: 2 });
  const { syncNow } = await import('./syncClient.js');

  await syncNow();

  expect(mocks.publishStatus).toHaveBeenCalledWith('Sync protocol changed; refresh the app');
});

it('distinguishes an HTTP rejection from an unavailable server', async () => {
  mocks.request.mockRejectedValue(Object.assign(new Error('Invalid request payload'), { status: 422 }));
  await (await import('./syncClient.js')).syncNow();
  expect(mocks.publishStatus).toHaveBeenCalledWith('Sync request failed (HTTP 422): Invalid request payload');
});
