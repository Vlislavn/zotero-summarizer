// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import usePaperReview from './usePaperReview.js';
import { cacheResponse, pendingMutations, publishStatus, savePapers } from '../offlineStore.js';

beforeEach(async () => { await publishStatus('', 'unchecked'); });

const clients = [];
function mount(key, initial) {
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false }, mutations: { retry: false },
  } });
  clients.push(client);
  if (initial) client.setQueryData(['review-detail', key], initial);
  const wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
  return { ...renderHook(() => usePaperReview(key), { wrapper }), client };
}
function disconnect() {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  onlineManager.setOnline(false);
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Disconnected test transport')));
}
async function changes(key) {
  return (await pendingMutations()).filter(row => row.item_key === key);
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach(client => client.clear());
  onlineManager.setOnline(true);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('opens the existing IndexedDB detail with empty query memory while offline', async () => {
  const key = 'COLD_OFFLINE_DETAIL';
  const detail = { item_key: key, title: 'Retained paper', user_note: 'Manual note', verdict: null };
  await cacheResponse(`review:${key}`, detail);
  disconnect();
  const first = mount(key);
  await waitFor(() => expect(first.result.current.detail).toEqual(detail));
  first.unmount();
  first.client.clear();
  const second = mount(key);
  await waitFor(() => expect(second.result.current.detail).toEqual(detail));
  expect(second.result.current.error).toBeNull();
});

it('refreshes a locally changed detail without waiting for connectivity', async () => {
  const key = 'REFRESH_OFFLINE_DETAIL';
  const before = { item_key: key, user_note: 'Before' };
  const after = { item_key: key, user_note: 'After local save' };
  await cacheResponse(`review:${key}`, before);
  disconnect();
  const hook = mount(key, before);
  await cacheResponse(`review:${key}`, after);
  act(() => hook.result.current.refreshDetail());
  await waitFor(() => expect(hook.result.current.detail).toEqual(after));
});

it('persists ordered verdicts and retraction offline without attempting a secondary tag', async () => {
  const key = 'ORDERED_OFFLINE_VERDICTS';
  const detail = { item_key: key, title: 'Retained detail', user_note: 'Manual', verdict: null };
  await savePapers([{ item_key: key, revisions: { verdict: 4 } }]);
  await cacheResponse(`review:${key}`, detail);
  disconnect();
  const hook = mount(key, detail);
  for (const user_priority of ['should_read', 'could_read', 'dont_read']) {
    act(() => hook.result.current.verdict.onSubmit({ user_priority, comment: user_priority }));
    await waitFor(() => expect(hook.result.current.detail?.verdict?.user_priority).toBe(user_priority));
    expect(hook.result.current.verdict.submitWarning).toBeNull();
    expect(hook.result.current.verdict.submitError).toBeNull();
  }
  act(() => hook.result.current.verdict.onDelete());
  await waitFor(() => expect(hook.result.current.verdict.deleting).toBe(false));
  await waitFor(async () => expect((await changes(key)).length).toBe(4));
  await waitFor(() => expect(hook.result.current.detail.verdict).toBeNull());
  const rows = await changes(key);
  expect(rows.map(row => [row.operation, row.value])).toEqual([
    ['set', 'should_read'], ['set', 'could_read'], ['set', 'dont_read'], ['delete', null],
  ]);
  expect(rows.every(row => row.base_revision === 4)).toBe(true);
  expect(new Set(rows.map(row => row.mutation_id)).size).toBe(4);
  expect(fetch.mock.calls.every(([path]) => String(path).startsWith('/api/golden/'))).toBe(true);
  hook.unmount();
  hook.client.clear();
  const remounted = mount(key);
  await waitFor(() => expect(remounted.result.current.detail?.user_note).toBe('Manual'));
  expect(remounted.result.current.detail.verdict).toBeNull();
  expect(await changes(key)).toEqual(rows);
});

it('refreshes a durable deletion locally once its transport outage is known', async () => {
  const key = 'DELETION_REFRESH_BOUNDARY';
  const detail = { item_key: key, user_note: 'Retained', verdict: { user_priority: 'could_read' } };
  await savePapers([{ item_key: key, revisions: { verdict: 4 } }]);
  await cacheResponse(`review:${key}`, detail);
  disconnect();
  const reads = [];
  fetch.mockImplementation((_path, options) => options?.method === 'DELETE'
    ? Promise.reject(new TypeError('Disconnected test transport'))
    : new Promise((_resolve, reject) => { reads.push(reject); }));
  const hook = mount(key, detail);
  act(() => hook.result.current.verdict.onDelete());
  await waitFor(async () => expect((await changes(key)).length).toBe(1));
  await waitFor(() => expect(hook.result.current.verdict.deleting).toBe(false));
  await waitFor(() => expect(hook.result.current.detail.verdict).toBeNull());
  expect(reads).toHaveLength(1); // the initial read; known outage avoids another blocking request
  await act(async () => { reads.forEach(reject => reject(new TypeError('Disconnected test transport'))); });
});

it('settles a missing local detail as a transport error instead of remaining paused', async () => {
  disconnect();
  const hook = mount('UNCACHED_OFFLINE_DETAIL');
  await waitFor(() => expect(hook.result.current.error?.message).toBe('Disconnected test transport'));
  expect(hook.result.current.detail).toBeUndefined();
  expect(hook.result.current.isLoading).toBe(false);
});

it('does not use a cached detail to hide an HTTP refusal', async () => {
  const key = 'OFFLINE_HTTP_REFUSAL';
  await cacheResponse(`review:${key}`, { item_key: key, user_note: 'Retained' });
  disconnect();
  fetch.mockResolvedValue({ ok: false, status: 403, statusText: 'Forbidden',
    json: async () => ({ detail: 'Forbidden' }) });
  const hook = mount(key);
  await waitFor(() => expect(hook.result.current.error?.status).toBe(403));
  expect(hook.result.current.detail).toBeUndefined();
});

it('does not queue a rejected server verdict as an offline success', async () => {
  const key = 'OFFLINE_VERDICT_REFUSAL';
  disconnect();
  fetch.mockResolvedValue({ ok: false, status: 403, statusText: 'Forbidden',
    json: async () => ({ detail: 'Forbidden' }) });
  const hook = mount(key, { item_key: key, verdict: null });
  act(() => hook.result.current.verdict.onSubmit({ user_priority: 'must_read', comment: '' }));
  await waitFor(() => expect(hook.result.current.verdict.submitError).toContain('HTTP 403'));
  expect(hook.result.current.verdict.submitNotice).toBeNull();
  expect(await changes(key)).toEqual([]);
});
