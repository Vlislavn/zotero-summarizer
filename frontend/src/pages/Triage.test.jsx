// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Triage from './Triage.jsx';
import { fetchJobs, fetchJob, fetchCalibrationMetrics, fetchLatestResultFeedback } from '../api/triageApi.js';

vi.mock('../api/triageApi.js', () => ({
  fetchJobs: vi.fn(), fetchJob: vi.fn(), fetchCalibrationMetrics: vi.fn(), cancelJob: vi.fn(), submitResultFeedback: vi.fn(), fetchLatestResultFeedback: vi.fn(),
}));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });

it('labels observed triage feedback without claiming an unbiased gate audit', async () => {
  fetchJobs.mockResolvedValue({ items: [] });
  fetchCalibrationMetrics.mockResolvedValue({ periods: {
    last_7d: { total_feedback: 4, with_prediction_count: 3, agreement_rate: 0, recall: 0, false_negative_count: 2 },
    last_30d: { total_feedback: 1, with_prediction_count: 0, agreement_rate: null, recall: null, false_negative_count: 0 },
    all_time: { total_feedback: 4, with_prediction_count: 3, agreement_rate: .5, recall: .5, false_negative_count: 2 },
  } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => { render(<QueryClientProvider client={client}><Triage /></QueryClientProvider>); });

  expect(screen.getByRole('heading', { name: 'Triage feedback' })).toBeTruthy();
  expect(screen.getByText(/Only reviewed items are included/).textContent).toContain('not unbiased ML-gate metrics');
  expect(screen.getAllByText('Predictions available: 3 / 4')).toHaveLength(2);
  expect(screen.getByText('Predictions available: 0 / 1')).toBeTruthy();
  expect(screen.getAllByText(/Observed recall:/).map((el) => el.textContent)).toEqual([
    'Observed recall: 0%', 'Observed recall: n/a', 'Observed recall: 50%',
  ]);
  expect(screen.queryByText('Gate recall:')).toBeNull();
  expect(screen.queryByText('Gate misses (audit FN):')).toBeNull();
  expect(screen.queryByTitle(/Counterfactual-audit/)).toBeNull();
  client.clear();
});

it('lists active papers and keeps polling during cancellation until the threads drain', async () => {
  vi.useFakeTimers();
  let job = { job_id: 'j1', status: 'running', completed: 0, total: 2, results: [], errors: [],
    active_items: [{ item_key: 'A', title: 'Working A' }, { item_key: 'B', title: 'Working B' }],
    current_title: 'Stale completed title' };
  fetchJobs.mockImplementation(async () => ({ items: [job] }));
  fetchJob.mockImplementation(async () => job);
  fetchCalibrationMetrics.mockResolvedValue(null);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  await act(async () => { render(<QueryClientProvider client={client}><Triage /></QueryClientProvider>); });

  let active = screen.getByRole('list', { name: 'Active papers' });
  expect(within(active).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['Working A', 'Working B']);
  expect(screen.queryByText('Stale completed title')).toBeNull();
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0');
  const calibrationCalls = fetchCalibrationMetrics.mock.calls.length;
  job = { ...job, status: 'cancelling', active_items: [{ item_key: 'B', title: 'Working B' }] };
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  active = screen.getByRole('list', { name: 'Active papers' });
  expect(within(active).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['Working B']);
  expect(fetchCalibrationMetrics).toHaveBeenCalledTimes(calibrationCalls);
  job = { ...job, status: 'cancelled', active_items: [] };
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(screen.queryByRole('list', { name: 'Active papers' })).toBeNull();
  expect(fetchCalibrationMetrics).toHaveBeenCalledTimes(calibrationCalls + 1);
  const calls = fetchJob.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(fetchJob).toHaveBeenCalledTimes(calls);
  client.clear();
});

it('restores durable feedback so reviewed results stay out of the todo filter', async () => {
  fetchJobs.mockResolvedValue({ items: [{ job_id: 'J1', status: 'completed' }] });
  fetchJob.mockResolvedValue({
    job_id: 'J1', status: 'completed', completed: 1, total: 1,
    active_items: [], errors: [],
    results: [{ item_key: 'PAPER-1', title: 'A saved decision', reading_priority: 'should_read' }],
  });
  fetchLatestResultFeedback.mockResolvedValue({
    items: [{ item_id: 'PAPER-1', verdict: 'approve' }],
  });
  fetchCalibrationMetrics.mockResolvedValue(null);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Triage /></QueryClientProvider>);

  expect(await screen.findByText('Approved by you')).toBeTruthy();
  expect(fetchLatestResultFeedback).toHaveBeenCalledWith(['PAPER-1']);
  fireEvent.click(screen.getByLabelText('Needs feedback only'));
  expect(await screen.findByText('All completed items have your feedback. ✓')).toBeTruthy();
  expect(screen.queryByText('A saved decision')).toBeNull();
  client.clear();
});
