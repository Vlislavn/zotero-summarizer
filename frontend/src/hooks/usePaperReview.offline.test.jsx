// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import usePaperReview from './usePaperReview.js';
import { cacheReviewDetail, pendingMutations, queueQuestionSubmission } from '../offlineStore.js';

const clients = [];
function mount(key) {
  // Match existing hook-test retry policy; do not change application defaults.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
  return { ...renderHook(() => usePaperReview(key), { wrapper }), client };
}
function disconnect() {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  onlineManager.setOnline(false);
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Synthetic disconnected transport')));
}
function submission(key, suffix) {
  return { submission_id: `00000000-0000-4000-8000-0000000000${suffix}`, item_key: key,
    question: 'Original device question', origin: 'typed', preset_id: null };
}
const questionIDs = (detail) => detail?.questions?.map(row => row.submission_id);

afterEach(() => {
  cleanup();
  clients.splice(0).forEach(client => client.clear());
  onlineManager.setOnline(true);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('refreshes the real durable question union while the query scheduler is offline', async () => {
  const key = 'OFFLINE_REFRESH';
  const detail = { item_key: key, user_note: 'Keep manual', questions: [] };
  onlineManager.setOnline(true);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(detail) }));
  const hook = mount(key);
  await waitFor(() => expect(hook.result.current.detail).toEqual(detail));
  disconnect();
  const payload = submission(key, '48');
  await queueQuestionSubmission(payload);
  act(() => hook.result.current.refreshDetail());
  await waitFor(() => expect(questionIDs(hook.result.current.detail)).toEqual([payload.submission_id]));
  expect(hook.result.current.detail.user_note).toBe('Keep manual');
  expect((await pendingMutations()).find(row => row.mutation_id === payload.submission_id).value).toEqual(payload);
});

it('loads the real IndexedDB union with empty React Query memory after an offline remount', async () => {
  const key = 'OFFLINE_REMOUNT';
  const payload = submission(key, '49');
  await cacheReviewDetail(key, { item_key: key, user_note: 'Persisted manual', questions: [] });
  await queueQuestionSubmission(payload);
  disconnect();
  const first = mount(key);
  await waitFor(() => expect(questionIDs(first.result.current.detail)).toEqual([payload.submission_id]));
  first.unmount();
  first.client.clear();
  const second = mount(key);
  await waitFor(() => expect(questionIDs(second.result.current.detail)).toEqual([payload.submission_id]));
  expect(second.result.current.detail.user_note).toBe('Persisted manual');
  expect(second.result.current.error).toBeNull();
});

it('settles the existing transport error offline when no cached detail exists', async () => {
  disconnect();
  const { result } = mount('OFFLINE_NO_CACHE');
  await waitFor(() => expect(result.current.error?.message).toBe('Synthetic disconnected transport'));
  expect(result.current.detail).toBeUndefined();
  expect(result.current.isLoading).toBe(false);
});

it.each(['http', 'malformed'])('does not invent cached success for a %s server failure', async (failure) => {
  const key = `OFFLINE_ERROR_${failure}`;
  await cacheReviewDetail(key, { item_key: key, user_note: 'Not a fallback for server errors', questions: [] });
  disconnect();
  fetch.mockResolvedValue(failure === 'http'
    ? { ok: false, status: 403, json: async () => ({ detail: 'Forbidden' }) }
    : { ok: true, json: async () => ({ item_key: key, questions: 'invalid' }) });
  const { result } = mount(key);
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(result.current.detail).toBeUndefined();
  if (failure === 'http') expect(result.current.error.status).toBe(403);
  else expect(result.current.error.message).toMatch(/Invalid review questions/);
});
