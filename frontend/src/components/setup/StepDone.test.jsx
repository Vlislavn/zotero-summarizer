// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
const navigate = vi.hoisted(() => vi.fn());
const verify = vi.hoisted(() => vi.fn());

vi.mock('react-router-dom', async (original) => ({
  ...await original(), useNavigate: () => navigate,
}));

vi.mock('../../api/setupApi.js', () => ({ fetchDoctorStatus: vi.fn(), runDoctor: verify, cancelDoctor: vi.fn() }));

import StepDone from './StepDone.jsx';
import { fetchDoctorStatus } from '../../api/setupApi.js';
beforeEach(() => {
  vi.clearAllMocks();
  verify.mockReset();
  fetchDoctorStatus.mockReset().mockResolvedValue({ ready: false, status: 'not_started', checks: [] });
  const values = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
    clear: () => values.clear(),
  });
});

afterEach(() => cleanup());

it('offers the real AI recovery destination without verification, save, consent or dismissal', async () => {
  fetchDoctorStatus.mockResolvedValue({ ready: false, status: 'needs_action', checks: [{
    id: 'llm_inference', status: 'needs_action', message: 'Synthetic unknown admission',
    recovery: { action: 'open_ai_settings', label: 'Review endpoint settings' },
  }] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><StepDone /></MemoryRouter></QueryClientProvider>);
  expect((await screen.findByRole('link', { name: 'Review endpoint settings' })).getAttribute('href')).toBe('/settings#ai-models');
  expect(navigate).not.toHaveBeenCalled();
  expect(verify).not.toHaveBeenCalled();
  expect(window.localStorage.getItem('zs:setupDismissed')).toBeNull();
  expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true);
  client.clear();
});

it('blocks Open Today during an explicitly requested recheck of an earlier Ready result', async () => {
  fetchDoctorStatus.mockResolvedValueOnce({ ready: true });
  let finish;
  verify.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><MemoryRouter><StepDone /></MemoryRouter></QueryClientProvider>);
  try {
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Verify setup' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true));
    expect(screen.queryByText('Setup verified. Your pipeline is ready.')).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  } finally {
    finish({ ready: false });
    view.unmount();
    client.clear();
  }
});

it('does not claim completion or open Today until a real Doctor result is ready', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><StepDone /></MemoryRouter></QueryClientProvider>);

  expect(screen.getByRole('heading', { name: 'Setup saved' })).toBeTruthy();
  expect(await screen.findByRole('button', { name: 'Verify setup' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true);
  await waitFor(() => expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false));
  client.setQueryData(['setup-doctor'], { ready: true });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Open Today' }));
  expect(window.localStorage.getItem('zs:setupDismissed')).toBe('1');
  expect(navigate).toHaveBeenCalledWith('/today');
  client.clear();
});
