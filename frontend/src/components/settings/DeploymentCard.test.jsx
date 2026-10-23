// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/setupApi.js', () => ({
  fetchDoctorStatus: vi.fn(),
  runDoctor: vi.fn(),
  cancelDoctor: vi.fn(),
}));

import { fetchDoctorStatus, runDoctor, cancelDoctor } from '../../api/setupApi.js';
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

function renderChecklist() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><DoctorChecklist /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchDoctorStatus.mockResolvedValue(DATA);
});

afterEach(cleanup);

it('keeps only actionable hosted results expanded', async () => {
  renderChecklist();

  expect(await screen.findByText('Inference failed')).toBeTruthy();
  expect(screen.getByText('2 checks passed')).toBeTruthy();
  expect(screen.queryByText('Local inference')).toBeNull();
  expect(screen.queryByText('Browser automation is optional')).toBeNull();
  expect(screen.getByText('Technical details')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Retry inference' }));
  await waitFor(() => expect(runDoctor).toHaveBeenCalledWith(['llm_inference']));
});

it('acknowledges a long verification immediately', async () => {
  runDoctor.mockReturnValue(new Promise(() => {}));
  renderChecklist();

  fireEvent.click(await screen.findByRole('button', { name: 'Verify setup' }));
  expect(await screen.findByText(/verification can take a few minutes/i)).toBeTruthy();
  expect(runDoctor).toHaveBeenCalledWith(null);
  expect(screen.getByRole('button', { name: 'Verifying…' }).disabled).toBe(true);
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

it('offers real settings recovery separately from a scoped retry for unknown local admission', async () => {
  fetchDoctorStatus.mockResolvedValue({ ready: false, checks: [{
    id: 'llm_inference', status: 'needs_action', message: 'Local envelope unknown',
    detail: 'No inference submitted', recovery: { label: 'Review AI settings', action: 'open_ai_settings' },
  }] });
  renderChecklist();

  expect((await screen.findByRole('link', { name: /Review AI settings/ })).getAttribute('href')).toBe('/settings');
  expect(runDoctor).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(runDoctor).toHaveBeenCalledWith(['llm_inference']));
});

it('stops only the captured running verification and never claims remote termination', async () => {
  fetchDoctorStatus.mockResolvedValue({ ready: false, status: 'running', run_id: 'actual-run', checks: [] });
  cancelDoctor.mockResolvedValue({ ready: false });
  renderChecklist();

  fireEvent.click(await screen.findByRole('button', { name: 'Stop verification' }));

  await waitFor(() => expect(cancelDoctor.mock.calls[0]?.[0]).toBe('actual-run'));
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
});
