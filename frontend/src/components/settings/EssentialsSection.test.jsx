// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/setupApi.js', () => ({ updatePaths: vi.fn() }));
vi.mock('../../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
import { updatePaths } from '../../api/setupApi.js';
import EssentialsSection from './EssentialsSection.jsx';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it.each([[true, undefined], [false, 422], [false, 403]])('invalidates old Doctor Ready only after a successful Zotero path save: %s status %s', async (saved, status) => {
  if (saved) updatePaths.mockResolvedValueOnce({ restart_required: true });
  else updatePaths.mockRejectedValueOnce(Object.assign(new Error('path is not readable'), { status }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(['setup-doctor'], { ready: true });
  render(<QueryClientProvider client={client}><EssentialsSection
    form={{ research_goals_text: 'Clinical agents', triage_criteria_text: '', output_language: 'English' }}
    onUpdate={vi.fn()} pathForm={{ zotero_data_dir: '/synthetic/zotero', pdf_root: '' }} onUpdatePath={vi.fn()}
  /></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Save paths' }));

  await waitFor(() => expect(updatePaths).toHaveBeenCalledTimes(1));
  expect(updatePaths.mock.calls[0][0]).toEqual({ zotero_data_dir: '/synthetic/zotero' });
  expect(await screen.findByText(saved ? 'Restart required to apply new paths.' : 'path is not readable')).toBeTruthy();
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(!saved);
  client.clear();
});

it.each([undefined, 503, 404])('retires Doctor after uncertain path save status %s while retaining the error', async (status) => {
  updatePaths.mockRejectedValueOnce(Object.assign(new Error('Path save acknowledgement unavailable'), { status }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(['setup-doctor'], { ready: true });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(<QueryClientProvider client={client}><EssentialsSection
    form={{ research_goals_text: 'Clinical agents', triage_criteria_text: '', output_language: 'English' }}
    onUpdate={vi.fn()} pathForm={{ zotero_data_dir: '/synthetic/zotero', pdf_root: '' }} onUpdatePath={vi.fn()}
  /></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Save paths' }));
  await screen.findByText('Path save acknowledgement unavailable');
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['setup-doctor'] });
  expect(updatePaths).toHaveBeenCalledTimes(1);
  expect(updatePaths.mock.calls[0][0]).toEqual({ zotero_data_dir: '/synthetic/zotero' });
  expect(screen.queryByText('Restart required to apply new paths.')).toBeNull();
  client.clear();
});
