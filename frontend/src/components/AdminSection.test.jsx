// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { focusManager, onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fetchConfig, getJob, refreshLabels, retrain } from '../api/settingsApi.js';
import AdminSection from './AdminSection.jsx';

vi.mock('../api/settingsApi.js', () => ({ fetchConfig: vi.fn(), getJob: vi.fn(), refreshLabels: vi.fn(), retrain: vi.fn() }));
let client;
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  fetchConfig.mockResolvedValue({ classifier_gate: { model_name: 'logreg' } });
  refreshLabels.mockResolvedValue({ ok: true });
  retrain.mockResolvedValue({ job_id: 'job-current', status: 'running' });
});
afterEach(() => {
  cleanup();
  client?.clear();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const running = { job_id: 'job-current', status: 'running', progress: { done: 1, total: 5 } };
function terminal(status) {
  return { ...running, status, finished_at: '2026-05-04T12:00:00Z',
    result: status === 'succeeded' ? { classifier_name: 'logreg', n_train: 20, n_holdout: 5, thresholds: {} } : null,
    error: status === 'failed' ? 'Training failed on the server' : null };
}
async function tick(milliseconds = 20) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}
async function start() {
  // Match the app's retry default so query-local refusal is observable.
  client = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false } } });
  render(<QueryClientProvider client={client}><AdminSection /></QueryClientProvider>);
  await tick();
  fireEvent.click(screen.getByRole('button', { name: 'Retrain model' }));
  await tick();
}

it.each(['pending', 'failed', 'missing'])('does not refresh labels or train with %s configuration', async state => {
  if (state === 'pending') fetchConfig.mockReturnValue(new Promise(() => {}));
  if (state === 'failed') fetchConfig.mockRejectedValue(new Error('Config unavailable'));
  if (state === 'missing') fetchConfig.mockResolvedValue({});
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><AdminSection /></QueryClientProvider>);
  await tick();
  const button = screen.getByRole('button', { name: 'Retrain model' });
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  await tick();
  expect(refreshLabels).not.toHaveBeenCalled();
  expect(retrain).not.toHaveBeenCalled();
  expect(screen.queryByText('logreg')).toBeNull();
});

it('captures the acknowledged classifier before label refresh, not a later configuration', async () => {
  let release;
  fetchConfig.mockResolvedValue({ classifier_gate: { model_name: 'configured-gate' } });
  refreshLabels.mockReturnValue(new Promise(resolve => { release = resolve; }));
  getJob.mockResolvedValue(terminal('succeeded'));
  await start();
  expect(refreshLabels).toHaveBeenCalledOnce();
  await act(async () => {
    client.setQueryData(['runtime-config'], { classifier_gate: { model_name: 'later-gate' } });
    release({ ok: true });
  });
  await tick();
  expect(retrain).toHaveBeenCalledWith({ classifier_name: 'configured-gate', n_folds: 5 });
});

it.each(['succeeded', 'failed'])('stops stale-running reads, then explicitly recovers the same job to %s', async (status) => {
  getJob.mockResolvedValueOnce(running).mockRejectedValueOnce(new Error('HTTP 503: status unavailable'))
    .mockResolvedValue(terminal(status));
  await start();
  expect(screen.getByText('Training (fold 1/5)…')).toBeTruthy();
  expect(getJob).toHaveBeenCalledTimes(1);
  await tick(2020);
  expect(screen.getByText(/Status unavailable — the server job may still be running/)).toBeTruthy();
  expect(screen.queryByText('Training (fold 1/5)…')).toBeNull();
  expect(screen.queryByRole('progressbar')).toBeNull();
  expect(client.getQueryData(['admin-job', 'job-current']).status).toBe('running');
  const startButton = screen.getByRole('button', { name: 'Retrain model' });
  expect(startButton.disabled).toBe(true);
  await tick(10_000);
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
  });
  await tick();
  expect(getJob).toHaveBeenCalledTimes(2);
  expect(retrain).toHaveBeenCalledTimes(1);
  expect(refreshLabels).toHaveBeenCalledTimes(1);
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  fireEvent.click(screen.getByRole('button', { name: 'Retry status' }));
  await tick();
  expect(getJob.mock.calls.map(([jobId]) => jobId)).toEqual(['job-current', 'job-current', 'job-current']);
  expect(screen.queryByText(/Status unavailable/)).toBeNull();
  if (status === 'succeeded') expect(screen.getByText(/Trained logreg on 20 rows/)).toBeTruthy();
  else expect(screen.getByText('Training failed on the server')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Retrain model' }).disabled).toBe(false);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['admin-model-card'] });
  await tick(6000);
  expect(getJob).toHaveBeenCalledTimes(3);
  expect(retrain).toHaveBeenCalledTimes(1);
  expect(refreshLabels).toHaveBeenCalledTimes(1);
});

it('blocks duplicate starts before the first status and resumes ordinary polling only after a successful explicit read', async () => {
  let rejectFirst;
  getJob.mockReturnValueOnce(new Promise((_resolve, reject) => { rejectFirst = reject; }))
    .mockResolvedValueOnce(running).mockResolvedValue(terminal('succeeded'));
  await start();
  const startButton = screen.getByRole('button', { name: 'Retrain model' });
  expect(startButton.disabled).toBe(true);
  fireEvent.click(startButton);
  expect(retrain).toHaveBeenCalledTimes(1);
  await act(async () => { rejectFirst(new Error('First status lost')); });
  await tick();
  expect(screen.getByText(/Status unavailable — the server job may still be running/)).toBeTruthy();
  await tick(6000);
  expect(getJob).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry status' }));
  await tick();
  expect(screen.getByText('Training (fold 1/5)…')).toBeTruthy();
  expect(screen.queryByText(/Status unavailable/)).toBeNull();
  await tick(2020);
  expect(screen.getByText(/Trained logreg on 20 rows/)).toBeTruthy();
  expect(getJob).toHaveBeenCalledTimes(3);
  expect(retrain).toHaveBeenCalledTimes(1);
  expect(refreshLabels).toHaveBeenCalledTimes(1);
});

it('keeps a failed manual status read paused rather than starting training or another poll loop', async () => {
  getJob.mockResolvedValueOnce(running).mockRejectedValue(new Error('Status still unavailable'));
  await start();
  await tick(2020);
  fireEvent.click(screen.getByRole('button', { name: 'Retry status' }));
  await tick();
  expect(screen.getByText(/Status unavailable — the server job may still be running/)).toBeTruthy();
  await tick(10_000);
  expect(getJob).toHaveBeenCalledTimes(3);
  expect(retrain).toHaveBeenCalledTimes(1);
  expect(refreshLabels).toHaveBeenCalledTimes(1);
});
