// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/setupApi.js', () => ({
  fetchDoctorStatus: vi.fn(),
  runDoctor: vi.fn(),
}));

import { fetchDoctorStatus, runDoctor } from '../../api/setupApi.js';
import { DoctorChecklist } from './DeploymentCard.jsx';

const DATA = {
  ready: false,
  modes: { local_inference: 'unavailable', offline_ready: 'needs_action' },
  checks: [
    { id: 'environment', status: 'ready', message: 'App environment is writable' },
    { id: 'zotero', status: 'ready', message: 'Zotero metadata is readable' },
    { id: 'llm_inference', status: 'needs_action', message: 'Inference failed',
      detail: 'configured provider/model timeout', recovery: { label: 'Retry inference' } },
    { id: 'optional_extras', status: 'unavailable', message: 'Browser automation is optional' },
  ],
};

function renderChecklist(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  return { ...render(<QueryClientProvider client={client}><DoctorChecklist {...props} /></QueryClientProvider>), client };
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchDoctorStatus.mockResolvedValue(DATA);
});

afterEach(cleanup);

it('typed AI recovery navigates without another verification or save', async () => {
  const open = vi.fn();
  fetchDoctorStatus.mockResolvedValueOnce({ ...DATA, checks: [{
    id: 'llm_inference', status: 'needs_action', message: 'Local capacity unknown',
    load_receipts: [{ admission: { status: 'unknown' }, reason_code: 'policy_missing' }],
    recovery: { label: 'Review endpoint settings', action: 'open_ai_settings' },
  }] });
  renderChecklist({ onOpenAiSettings: open });
  fireEvent.click(await screen.findByRole('button', { name: 'Review endpoint settings' }));
  expect(open).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Local load safety is not established/)).toBeTruthy();
  expect(runDoctor).not.toHaveBeenCalled();
});

it('shows the complete checklist without hiding unavailable optional components', async () => {
  renderChecklist();

  expect(await screen.findByText('Inference failed')).toBeTruthy();
  expect(screen.getByText('2 of 4 checks ready')).toBeTruthy();
  expect(screen.getByText('Local inference')).toBeTruthy();
  expect(screen.getByText('Browser automation is optional')).toBeTruthy();
  expect(screen.getAllByText('Unavailable')).toHaveLength(2);
  expect(screen.getByText('Technical details')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Retry inference' }));
  await waitFor(() => expect(runDoctor).toHaveBeenCalledWith(['llm_inference']));
});

it('acknowledges a long verification immediately', async () => {
  let finish;
  runDoctor.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  renderChecklist();

  try {
    fireEvent.click(await screen.findByRole('button', { name: 'Verify setup' }));
    expect(await screen.findByText(/verification can take a few minutes/i)).toBeTruthy();
    expect(runDoctor).toHaveBeenCalledWith(null);
    expect(screen.getByRole('button', { name: 'Verifying…' }).disabled).toBe(true);
  } finally {
    finish(DATA);
  }
});

it('withholds earlier Ready after a lost verification reply until status is read again', async () => {
  fetchDoctorStatus.mockResolvedValueOnce({ ready: true, status: 'ready', modes: {}, checks: [] });
  runDoctor.mockRejectedValueOnce(new Error('response lost'));
  const view = renderChecklist();
  expect(await screen.findByText('✓ Setup verified')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Verify setup' }));
  expect(await screen.findByText(/Verification request failed: response lost/)).toBeTruthy();
  expect(view.client.getQueryData(['setup-doctor']).ready).toBe(false);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Read status' }));
  expect(await screen.findByText('Inference failed')).toBeTruthy();
  expect(runDoctor).toHaveBeenCalledTimes(1);
});

it('typed retry is scoped and does not pretend to execute recovery guidance', async () => {
  fetchDoctorStatus.mockResolvedValueOnce({ ...DATA, checks: [{
    id: 'runtime_model', status: 'needs_action', message: 'Model missing',
    recovery: { label: 'Choose a served model', action: 'retry' },
  }] });
  renderChecklist();
  expect(await screen.findByText('Choose a served model')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Retry check' }));
  await waitFor(() => expect(runDoctor).toHaveBeenCalledWith(['runtime_model']));
});

it('keeps fresh and local-capability states truthful', async () => {
  fetchDoctorStatus.mockResolvedValueOnce({ ready: false, modes: {}, checks: [] });
  const first = renderChecklist();
  expect(await screen.findByText('Verification has not run yet.')).toBeTruthy();
  expect(screen.queryByText('Local inference')).toBeNull();
  first.unmount();

  fetchDoctorStatus.mockResolvedValueOnce({
    ready: false,
    modes: { local_inference: 'ready', offline_ready: 'ready', strict_offline: 'not_started' },
    checks: [],
  });
  renderChecklist();
  expect(await screen.findByText('Local inference')).toBeTruthy();
  expect(screen.getByText('Offline-ready')).toBeTruthy();
});

it('opens the existing recovery control and keeps a separate scoped retry', async () => {
  const open = vi.fn();
  fetchDoctorStatus.mockResolvedValueOnce({ ...DATA, checks: [{
    id: 'rss_source', status: 'needs_action', message: 'No RSS source is enabled',
    recovery: { label: 'Add a source in Settings', action: 'open_sources_settings' },
  }] });
  renderChecklist({ onOpenSettings: open });
  fireEvent.click(await screen.findByRole('button', { name: 'Add a source in Settings' }));
  expect(open).toHaveBeenCalledWith('rss-feeds');
  expect(runDoctor).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry check' }));
  await waitFor(() => expect(runDoctor).toHaveBeenCalledWith(['rss_source']));
});

it('recovers a status read failure without starting model checks', async () => {
  fetchDoctorStatus.mockRejectedValueOnce(new Error('health service unavailable'));
  renderChecklist();
  expect(await screen.findByText(/health service unavailable/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Retry status' }));
  expect(await screen.findByText('Inference failed')).toBeTruthy();
  expect(runDoctor).not.toHaveBeenCalled();
});

it('labels unchecked rows and distinguishes offline contracts before verification', async () => {
  fetchDoctorStatus.mockResolvedValueOnce({ ready: false, modes: {
    local_inference: 'needs_action', offline_ready: 'needs_action', strict_offline: 'not_started',
  }, checks: [{ id: 'dry_run', status: 'not_started', message: 'Not checked yet' }] });
  renderChecklist();
  expect(await screen.findByText('Not checked yet')).toBeTruthy();
  expect(screen.getByText('Local inference')).toBeTruthy();
  expect(screen.getByText(/RSS and PDF acquisition may still use the internet/)).toBeTruthy();
  expect(screen.getByText(/optional network enrichment and remote fallbacks are disabled/)).toBeTruthy();
  expect(runDoctor).not.toHaveBeenCalled();
});

it('blocks duplicate verification after mounting an already running server check', async () => {
  fetchDoctorStatus.mockResolvedValue({ ...DATA, status: 'running', checks: [
    { id: 'environment', status: 'running', message: 'Server check in progress' },
    ...DATA.checks.filter((row) => row.status === 'needs_action'),
  ] });
  const view = renderChecklist();
  try {
    expect(await screen.findByText('Server check in progress')).toBeTruthy();
    const buttons = screen.getAllByRole('button');
    expect(buttons.every((button) => button.disabled)).toBe(true);
    for (const button of buttons) fireEvent.click(button);
    expect(runDoctor).not.toHaveBeenCalled();
  } finally {
    view.unmount();
    view.client.clear();
  }
});

it.each(['ready', 'needs_action'])('refreshes a resumed server run until %s without posting again', async (status) => {
  vi.useFakeTimers();
  let view;
  try {
    const running = { ...DATA, status: 'running', checks: [
      { id: 'environment', status: 'running', message: 'Server check in progress' },
    ] };
    const terminal = { ...DATA, status, ready: status === 'ready', checks: [] };
    fetchDoctorStatus.mockResolvedValueOnce(running).mockResolvedValue(terminal);
    view = renderChecklist();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const query = view.client.getQueryCache().find({ queryKey: ['setup-doctor'] });
    expect(query.state.data.status).toBe('running');
    expect(typeof query.options.refetchInterval).toBe('function');
    const interval = query.options.refetchInterval(query);
    expect(Number.isFinite(interval) && interval > 0).toBe(true);
    expect(fetchDoctorStatus).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(interval); });
    expect(query.state.data.status).toBe(status);
    expect(fetchDoctorStatus).toHaveBeenCalledTimes(2);
    expect(query.options.refetchInterval(query)).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(interval); });
    expect(fetchDoctorStatus).toHaveBeenCalledTimes(2);
    expect(runDoctor).not.toHaveBeenCalled();
    expect(Boolean(screen.queryByText('✓ Setup verified'))).toBe(status === 'ready');
  } finally {
    view?.unmount();
    view?.client.clear();
    vi.useRealTimers();
  }
});
