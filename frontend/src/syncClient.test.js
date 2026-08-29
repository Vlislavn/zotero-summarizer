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
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.pendingMutations.mockResolvedValue([]);
  mocks.getMeta.mockResolvedValue(0);
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});

it('surfaces a protocol mismatch instead of masking it as an outage', async () => {
  mocks.request.mockResolvedValue({ protocol: 2 });
  const { syncNow } = await import('./syncClient.js');

  await syncNow();

  expect(mocks.publishStatus).toHaveBeenCalledWith('Sync protocol changed; refresh the app');
});
