// @vitest-environment jsdom
import { createElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import usePaperReview from './usePaperReview.js';

const api = vi.hoisted(() => ({ submitVerdict: vi.fn(), deleteVerdict: vi.fn(), fetchReviewDetail: vi.fn(), queueRejectTag: vi.fn() }));
vi.mock('../api/goldenApi.js', () => ({ submitVerdict: api.submitVerdict, deleteVerdict: api.deleteVerdict, fetchReviewDetail: api.fetchReviewDetail }));
vi.mock('../api/libraryApi.js', () => ({ queueRejectTag: api.queueRejectTag }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function setup() {
  api.fetchReviewDetail.mockResolvedValue({ verdict: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onVerdictAcknowledged = vi.fn(), onSaved = vi.fn();
  const wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
  const hook = renderHook(() => usePaperReview('P1', { onVerdictAcknowledged, onSaved }), { wrapper });
  return { ...hook, client, onVerdictAcknowledged, onSaved };
}

it.each(['success', 'failure'])('acknowledges primary save once before secondary %s, preserving old completion', async secondary => {
  api.submitVerdict.mockResolvedValue({ verdict: { user_priority: 'dont_read' } });
  let finish;
  api.queueRejectTag.mockImplementation(() => new Promise((resolve, reject) => {
    finish = () => secondary === 'success' ? resolve({ queued: true }) : reject(new Error('Secondary tag failure'));
  }));
  const { result, client, onVerdictAcknowledged, onSaved } = setup();
  act(() => result.current.verdict.onSubmit({ user_priority: 'dont_read', comment: 'Changed' }));
  try {
    await waitFor(() => expect(api.queueRejectTag).toHaveBeenCalledOnce());
    expect(onVerdictAcknowledged).toHaveBeenCalledOnce();
    expect(onSaved).not.toHaveBeenCalled();
    expect(result.current.verdict.submitting).toBe(true);
  } finally {
    await act(async () => { finish?.(); });
  }
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(onVerdictAcknowledged).toHaveBeenCalledOnce();
  expect(result.current.verdict.submitError).toBeNull();
  if (secondary === 'failure') expect(result.current.verdict.submitWarning).toContain('reject tag was not queued');
  else expect(result.current.verdict.submitWarning).toBeNull();
  client.clear();
});

it('does not acknowledge a rejected primary request or start its secondary work', async () => {
  api.submitVerdict.mockRejectedValue(new Error('Primary refusal'));
  const { result, client, onVerdictAcknowledged, onSaved } = setup();
  act(() => result.current.verdict.onSubmit({ user_priority: 'dont_read', comment: 'Rejected' }));
  await waitFor(() => expect(result.current.verdict.submitError).toBe('Primary refusal'));
  expect(onVerdictAcknowledged).not.toHaveBeenCalled();
  expect(onSaved).not.toHaveBeenCalled();
  expect(api.queueRejectTag).not.toHaveBeenCalled();
  client.clear();
});

it('acknowledges a successful delete once while retaining its completion callback', async () => {
  api.deleteVerdict.mockResolvedValue({ deleted: true });
  const { result, client, onVerdictAcknowledged, onSaved } = setup();
  act(() => result.current.verdict.onDelete());
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(onVerdictAcknowledged).toHaveBeenCalledOnce();
  expect(api.deleteVerdict).toHaveBeenCalledWith('P1');
  expect(api.queueRejectTag).not.toHaveBeenCalled();
  expect(result.current.verdict.deleteError).toBeNull();
  client.clear();
});
