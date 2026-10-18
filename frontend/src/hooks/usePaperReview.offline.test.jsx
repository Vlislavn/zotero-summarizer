// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import usePaperReview from './usePaperReview.js';
import { applyPull, cacheReviewDetail, pendingMutations, queueQuestionSubmission, savePapers } from '../offlineStore.js';

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

it('commits ordered verdict changes and a retraction before reconnect, not in the paused query scheduler', async () => {
  const key = 'OFFLINE_VERDICT';
  await savePapers([{ item_key: key, revisions: { verdict: 4 } }]);
  await cacheReviewDetail(key, { item_key: key, title: 'Cached paper', user_note: 'Manual', questions: [] });
  disconnect();
  const first = mount(key);
  await waitFor(() => expect(first.result.current.detail?.title).toBe('Cached paper'));

  for (const user_priority of ['should_read', 'could_read', 'dont_read']) {
    act(() => first.result.current.verdict.onSubmit({ user_priority, comment: `Reason: ${user_priority}` }));
    await waitFor(() => expect(first.result.current.detail?.verdict?.user_priority).toBe(user_priority));
    expect(first.result.current.verdict.submitNotice).toMatch(/Saved on this device/);
    expect(first.result.current.verdict.submitWarning).toBeNull();
  }
  act(() => first.result.current.verdict.onDelete());
  await waitFor(() => expect(first.result.current.detail?.verdict).toBeNull());
  const rows = (await pendingMutations()).filter(row => row.item_key === key);
  expect(rows.map(row => [row.operation, row.value])).toEqual([
    ['set', 'should_read'], ['set', 'could_read'], ['set', 'dont_read'], ['delete', null],
  ]);
  expect(new Set(rows.map(row => row.mutation_id)).size).toBe(4);
  expect(rows.every(row => row.base_revision === 4)).toBe(true);
  first.unmount();
  first.client.clear();
  const reopened = mount(key);
  await waitFor(() => expect(reopened.result.current.detail?.verdict).toBeNull());
  expect(reopened.result.current.detail.user_note).toBe('Manual');
  expect((await pendingMutations()).filter(row => row.item_key === key)).toEqual(rows);
});

it('opens compact synced context without requiring the paper to have been opened before going offline', async () => {
  const key = 'OFFLINE_COMPACT_SNAPSHOT';
  await applyPull({ cursor: 12, papers: [{ item_key: key, title: 'Synced but unopened',
    abstract_preview: 'Stored abstract excerpt', review_digest: { tldr: 'Stored compact finding' },
    review_note: 'Saved manual text', questions: [], verdict: { user_priority: 'should_read' },
  }] });
  disconnect();
  const { result } = mount(key);
  await waitFor(() => expect(result.current.detail?.title).toBe('Synced but unopened'));
  expect(result.current.detail).toMatchObject({
    offline_snapshot: true, abstract: 'Stored abstract excerpt', user_note: 'Saved manual text',
    deep_review: { digest: { tldr: 'Stored compact finding' } },
    verdict: { user_priority: 'should_read' },
  });
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
