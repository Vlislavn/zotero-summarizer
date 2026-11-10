// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderRouter } from '../../testRouter.jsx';
const api = vi.hoisted(() => ({ updatePaths: vi.fn() }));
vi.mock('../../api/setupApi.js', () => api);
vi.mock('../../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: { paths: {
  zotero_data_dir: { value: '/synthetic/running-zotero', set: true, exists: true }, pdf_root: { value: '/synthetic/running-pdf', set: true, exists: true },
} }, isError: false }) }));
import ZoteroPathsTask from './ZoteroPathsTask.jsx';
let client;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return renderRouter(<ZoteroPathsTask />, client, ['/settings?task=zotero']);
}
beforeEach(() => { api.updatePaths.mockResolvedValue({ restart_required: true, written: ['ZOTERO_DATA_DIR'] }); });
afterEach(() => { cleanup(); client?.clear(); vi.resetAllMocks(); });
it('does not offer an unnecessary path write/restart for a pristine task', async () => {
  mount(); await screen.findByDisplayValue('/synthetic/running-zotero'); expect(screen.getByRole('button', { name: 'Save paths' }).disabled).toBe(true);
});
it.each([['Zotero data directory', 'zotero_data_dir', '/synthetic/running-zotero'], ['PDF root (optional)', 'pdf_root', '/synthetic/running-pdf']])(
  'writes only the changed filled %s, never an untouched running-process value', async (_label, key, saved) => {
    mount(); const field = await screen.findByDisplayValue(saved);
    fireEvent.change(field, { target: { value: '/synthetic/explicit-replacement' } }); fireEvent.click(screen.getByRole('button', { name: 'Save paths' }));
    await waitFor(() => expect(api.updatePaths).toHaveBeenCalledTimes(1)); expect(api.updatePaths.mock.calls[0][0]).toEqual({ [key]: '/synthetic/explicit-replacement' });
  });
it('blank means keep, not an unsaved request to erase an existing path', async () => {
  mount(); await screen.findByDisplayValue('/synthetic/running-zotero'); fireEvent.change(screen.getByDisplayValue('/synthetic/running-pdf'), { target: { value: '' } });
  expect(screen.getByRole('button', { name: 'Save paths' }).disabled).toBe(true); expect(api.updatePaths).not.toHaveBeenCalled();
});
