// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { renderRouter } from '../../testRouter.jsx';
vi.mock('../../api/setupApi.js', () => ({ updatePaths: vi.fn() }));
vi.mock('../../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
import { updatePaths } from '../../api/setupApi.js';
import ZoteroPathsTask from './ZoteroPathsTask.jsx';
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function mount(client) {
  renderRouter(<ZoteroPathsTask />, client, ['/settings?task=zotero']);
  fireEvent.change(screen.getByLabelText('Zotero data directory'), { target: { value: '/synthetic/zotero' } });
}
it.each([[true, undefined], [false, 422], [false, 403]])('invalidates old Doctor Ready only after a successful Zotero path save: %s status %s', async (saved, status) => {
  if (saved) updatePaths.mockResolvedValueOnce({ restart_required: true, written: ['ZOTERO_DATA_DIR'] });
  else updatePaths.mockRejectedValueOnce(Object.assign(new Error('path is not readable'), { status }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(['setup-doctor'], { ready: true }); mount(client);
  fireEvent.click(screen.getByRole('button', { name: 'Save paths' }));
  await waitFor(() => expect(updatePaths).toHaveBeenCalledTimes(1));
  expect(updatePaths.mock.calls[0][0]).toEqual({ zotero_data_dir: '/synthetic/zotero' });
  expect(await screen.findByText(saved ? 'Paths written. Restart required to apply them; the running process is unchanged.' : 'path is not readable')).toBeTruthy();
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(!saved); client.clear();
});
it.each([undefined, 503, 404])('retires Doctor after uncertain path save status %s while retaining the error', async status => {
  updatePaths.mockRejectedValueOnce(Object.assign(new Error('Path save acknowledgement unavailable'), { status }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(['setup-doctor'], { ready: true }); const invalidate = vi.spyOn(client, 'invalidateQueries'); mount(client);
  fireEvent.click(screen.getByRole('button', { name: 'Save paths' })); await screen.findByText('Path save acknowledgement unavailable');
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['setup-doctor'] }); expect(updatePaths).toHaveBeenCalledTimes(1);
  expect(updatePaths.mock.calls[0][0]).toEqual({ zotero_data_dir: '/synthetic/zotero' });
  expect(screen.queryByText('Paths written. Restart required to apply them; the running process is unchanged.')).toBeNull(); client.clear();
});
