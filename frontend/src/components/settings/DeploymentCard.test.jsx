// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
      detail: 'kather/sota timeout', recovery: { label: 'Retry inference' } },
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
