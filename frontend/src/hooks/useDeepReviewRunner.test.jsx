// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import useDeepReviewRunner from './useDeepReviewRunner.js';
import { fetchDeepReviewStatus, runDeepReview } from '../api/libraryApi.js';

vi.mock('../api/libraryApi.js', () => ({ fetchDeepReviewStatus: vi.fn(), runDeepReview: vi.fn() }));
vi.mock('../api/settingsApi.js', () => ({ fetchLlmReachability: async () => ({ stages: [] }) }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const onDone = vi.fn();
  const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, onDone, wrapper };
}

it('stops the spinner on polling failure and resumes the same job on explicit retry', async () => {
  const { client, onDone, wrapper } = setup();
  fetchDeepReviewStatus.mockResolvedValue({ status: 'running' });
  runDeepReview.mockResolvedValue({ status: 'running' });
  const { result } = renderHook(() => useDeepReviewRunner('A', { onDone, autoRun: true }), { wrapper });
  await waitFor(() => expect(result.current.running).toBe(true));
  expect(runDeepReview).not.toHaveBeenCalled();
  fetchDeepReviewStatus.mockRejectedValue(new Error('Network failed'));
  await act(async () => { await client.refetchQueries({ queryKey: ['deep-review-status', 'A'] }); });
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(result.current.status.error).toContain('Network failed');
  act(() => result.current.run());
  await waitFor(() => expect(result.current.running).toBe(true));
  fetchDeepReviewStatus.mockResolvedValue({ status: 'ready' });
  await act(async () => { await client.refetchQueries({ queryKey: ['deep-review-status', 'A'] }); });
  await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  expect(result.current.running).toBe(false);
  expect(runDeepReview).toHaveBeenCalledOnce();
});

it('a late response from the previous paper cannot start a spinner on the next paper', async () => {
  const { client, wrapper } = setup();
  fetchDeepReviewStatus.mockResolvedValue({ status: 'idle' });
  let finish;
  runDeepReview.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const { result, rerender } = renderHook(({ key }) => useDeepReviewRunner(key), { wrapper, initialProps: { key: 'A' } });
  await waitFor(() => expect(fetchDeepReviewStatus).toHaveBeenCalledWith('A'));
  act(() => result.current.run());
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  rerender({ key: 'B' });
  await waitFor(() => expect(fetchDeepReviewStatus).toHaveBeenCalledWith('B'));
  await act(async () => { finish({ status: 'running' }); });
  expect(result.current.running).toBe(false);
  expect(client.getQueryData(['deep-review-status', 'B']).status).toBe('idle');
});

it('a delayed initial idle response cannot overwrite an accepted review', async () => {
  const { wrapper } = setup();
  let finishStatus;
  fetchDeepReviewStatus.mockImplementation(() => new Promise((resolve) => { finishStatus = resolve; }));
  runDeepReview.mockResolvedValue({ status: 'running' });
  const { result } = renderHook(() => useDeepReviewRunner('A'), { wrapper });
  await waitFor(() => expect(finishStatus).toBeTypeOf('function'));
  act(() => result.current.run());
  await waitFor(() => expect(result.current.status.status).toBe('running'));
  await act(async () => { finishStatus({ status: 'idle' }); });
  expect(result.current.status.status).toBe('running');
});
