// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import usePaperReview from './usePaperReview.js';

const api = vi.hoisted(() => ({ submitVerdict: vi.fn(), deleteVerdict: vi.fn() }));
vi.mock('../api/goldenApi.js', () => ({
  ...api, fetchReviewDetail: async (key) => ({ item_key: key, title: key, verdict: null }),
}));
vi.mock('../api/libraryApi.js', () => ({ queueRejectTag: vi.fn() }));
const clients = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.resetAllMocks(); });

it.each(['submit', 'delete'])('does not let an old %s completion mutate the next paper UI', async (operation) => {
  let finish;
  (operation === 'submit' ? api.submitVerdict : api.deleteVerdict)
    .mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const onSaved = vi.fn();
  const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(({ key }) => usePaperReview(key, { onSaved }), { wrapper, initialProps: { key: 'P1' } });
  await waitFor(() => expect(hook.result.current.detail?.item_key).toBe('P1'));
  act(() => operation === 'submit'
    ? hook.result.current.verdict.onSubmit({ user_priority: 'should_read', comment: 'P1 only' })
    : hook.result.current.verdict.onDelete());
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  hook.rerender({ key: 'P2' });
  await waitFor(() => expect(hook.result.current.detail?.item_key).toBe('P2'));
  await act(async () => { finish(operation === 'submit' ? { saved_offline: true } : { deleted: true }); });
  expect(hook.result.current.detail.item_key).toBe('P2');
  expect(hook.result.current.verdict.submitNotice).toBeNull();
  expect(onSaved).not.toHaveBeenCalled();
});
