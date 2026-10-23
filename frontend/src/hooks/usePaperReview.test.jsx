// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import usePaperReview from './usePaperReview.js';

const { submitVerdict, deleteVerdict, queueRejectTag, fetchReviewDetail, invalidateQueries } = vi.hoisted(() => ({
  submitVerdict: vi.fn(), deleteVerdict: vi.fn(), queueRejectTag: vi.fn(), fetchReviewDetail: vi.fn(), invalidateQueries: vi.fn(),
}));

vi.mock('../api/goldenApi.js', () => ({
  fetchReviewDetail, submitVerdict, deleteVerdict,
}));
vi.mock('../api/libraryApi.js', () => ({ queueRejectTag }));

afterEach(() => vi.clearAllMocks());

it('removes the green device-save notice after its offline verdict is rejected on sync', async () => {
  fetchReviewDetail.mockResolvedValue({ verdict: null });
  submitVerdict.mockResolvedValue({ saved_offline: true, queued: true });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
  const { result } = renderHook(() => usePaperReview('P1'), { wrapper });

  act(() => result.current.verdict.onSubmit({ user_priority: 'must_read', comment: 'offline' }));
  await waitFor(() => expect(result.current.verdict.submitNotice).toContain('Saved on this device'));
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, pending: 1, rejected: [],
  } })));
  expect(result.current.verdict.submitNotice).toContain('Saved on this device');
  act(() => window.dispatchEvent(new CustomEvent('zs-sync-status', { detail: {
    online: true, pending: 0, rejected: [{ item_key: 'P1', field: 'verdict', status: 'rejected' }],
  } })));
  await waitFor(() => expect(result.current.verdict.submitNotice).toBeNull());
  client.clear();
});

it('fences related context only on server verdict and deletion ACKs, not device drafts', async () => {
  fetchReviewDetail.mockResolvedValue({ verdict: null });
  const onVerdictAcknowledged = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
  const { result } = renderHook(() => usePaperReview('P1', { onVerdictAcknowledged }), { wrapper });

  submitVerdict.mockResolvedValueOnce({ saved_offline: true });
  act(() => result.current.verdict.onSubmit({ user_priority: 'dont_read', comment: '' }));
  await waitFor(() => expect(result.current.verdict.submitNotice).toContain('Saved on this device'));
  expect(queueRejectTag).not.toHaveBeenCalled();
  expect(onVerdictAcknowledged).not.toHaveBeenCalled();

  submitVerdict.mockResolvedValueOnce({ verdict: { user_priority: 'must_read' } });
  act(() => result.current.verdict.onSubmit({ user_priority: 'must_read', comment: '' }));
  await waitFor(() => expect(onVerdictAcknowledged).toHaveBeenCalledTimes(1));

  deleteVerdict.mockResolvedValueOnce({ saved_offline: true }).mockResolvedValueOnce({ deleted: true });
  act(() => result.current.verdict.onDelete());
  await waitFor(() => expect(result.current.verdict.deleting).toBe(false));
  expect(deleteVerdict).toHaveBeenCalledTimes(1);
  expect(onVerdictAcknowledged).toHaveBeenCalledTimes(1);
  act(() => result.current.verdict.onDelete());
  await waitFor(() => expect(onVerdictAcknowledged).toHaveBeenCalledTimes(2));
  client.clear();
});

it('keeps a saved verdict successful when the secondary reject tag fails', async () => {
  fetchReviewDetail.mockResolvedValue({ verdict: null });
  submitVerdict.mockResolvedValue({ verdict: { user_priority: 'dont_read' } });
  queueRejectTag.mockRejectedValue(new Error('offline tag queue'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.invalidateQueries = invalidateQueries;
  const wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
  const { result } = renderHook(() => usePaperReview('P1'), { wrapper });

  act(() => result.current.verdict.onSubmit({ user_priority: 'dont_read', comment: '' }));

  await waitFor(() => expect(result.current.verdict.submitWarning).toContain('reject tag was not queued'));
  expect(result.current.verdict.submitError).toBeNull();
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['review-detail', 'P1'] });
});
