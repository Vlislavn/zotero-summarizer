// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
const navigate = vi.hoisted(() => vi.fn());

vi.mock('react-router-dom', async (original) => ({
  ...await original(), useNavigate: () => navigate,
}));

vi.mock('../../api/setupApi.js', () => ({ fetchDoctorStatus: vi.fn().mockResolvedValue({ ready: false }) }));
vi.mock('../settings/DeploymentCard.jsx', () => ({
  DoctorChecklist: () => <button>Verify setup</button>,
}));

import StepDone from './StepDone.jsx';
beforeEach(() => {
  vi.clearAllMocks();
  const values = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
    clear: () => values.clear(),
  });
});

afterEach(() => cleanup());

it('does not claim completion or open Today until a real Doctor result is ready', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><StepDone /></MemoryRouter></QueryClientProvider>);

  expect(screen.getByRole('heading', { name: 'Setup saved' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Verify setup' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true);
  await waitFor(() => expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false));
  client.setQueryData(['setup-doctor'], { ready: true });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Open Today' }));
  expect(window.localStorage.getItem('zs:setupDismissed')).toBe('1');
  expect(navigate).toHaveBeenCalledWith('/today');
  client.clear();
});
