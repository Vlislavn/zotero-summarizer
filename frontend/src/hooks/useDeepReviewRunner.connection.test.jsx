// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ run: vi.fn() }));
vi.mock('../api/libraryApi.js', () => ({
  runDeepReview: api.run, fetchDeepReviewStatus: async () => ({ status: 'idle' }),
}));
vi.mock('../api/settingsApi.js', () => ({ fetchLlmReachability: async () => ({
  stages: [{ stage: 'deep_review', enabled: true, reachable: true }],
}) }));
const clients = [];
beforeEach(() => {
  vi.resetModules();
  api.run.mockReset();
  api.run.mockResolvedValue({ status: 'running' });
  vi.stubGlobal('indexedDB', new IDBFactory());
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); });

it('does not auto-run with cached positive model metadata while app sync is unconfirmed, then permits confirmed recovery', async () => {
  const offline = await import('../offlineStore.js');
  const { default: useDeepReviewRunner } = await import('./useDeepReviewRunner.js');
  await offline.publishStatus('Owned server stopped', 'unavailable');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(() => useDeepReviewRunner('P1', { autoRun: true }), { wrapper });
  await waitFor(() => expect(hook.result.current.llmChecked).toBe(true));
  expect(hook.result.current.llm.reachable).toBe(true);
  expect(hook.result.current.llmAvailable).toBe(false);
  expect(api.run).not.toHaveBeenCalled();
  await act(async () => { await offline.publishStatus('', 'connected'); });
  await waitFor(() => expect(api.run).toHaveBeenCalledOnce());
  expect(hook.result.current.llmAvailable).toBe(true);
});
