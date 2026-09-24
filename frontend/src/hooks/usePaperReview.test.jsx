// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import usePaperReview from './usePaperReview.js';

const { submitVerdict, queueRejectTag, fetchReviewDetail, invalidateQueries } = vi.hoisted(() => ({
  submitVerdict: vi.fn(), queueRejectTag: vi.fn(), fetchReviewDetail: vi.fn(), invalidateQueries: vi.fn(),
}));

vi.mock('../api/goldenApi.js', () => ({
  fetchReviewDetail, submitVerdict, deleteVerdict: vi.fn(),
}));
vi.mock('../api/libraryApi.js', () => ({ queueRejectTag }));

afterEach(() => vi.clearAllMocks());

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
