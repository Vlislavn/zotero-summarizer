// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ replaceConnectionCredential: vi.fn(), readConnectionCredential: vi.fn() }));
vi.mock('../../api/connectionsApi.js', () => api);
import ConnectionAuthorization from './ConnectionAuthorization.jsx';
const providers = [{ name: 'saved', base_url: 'http://127.0.0.1:9999/v1', auth_mode: 'auto' }];
let client;
let activity;
let saved;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); activity = vi.fn(); saved = vi.fn();
  const view = render(<QueryClientProvider client={client}><ConnectionAuthorization providers={providers} revision="r1" connectionId="synthetic-op" onSaved={saved} onActivityChange={activity} /></QueryClientProvider>);
  fireEvent.click(screen.getByText('Add an API key for this connection')); return view;
}
async function prepareReplacement() {
  fireEvent.click(screen.getByRole('button', { name: 'Review authorization impact' }));
  const input = await screen.findByLabelText('API key'); fireEvent.change(input, { target: { value: 'synthetic-not-a-real-credential' } }); return input;
}
beforeEach(() => { api.replaceConnectionCredential.mockReset().mockResolvedValue({ preview: 'preview', revision: 'r1', operation_id: 'synthetic-op', impact: { consumers: [] } }); });
afterEach(() => { cleanup(); client?.clear(); vi.resetAllMocks(); });
it('reports only dirty/pending booleans to the enclosing guard; secret text stays within its credential owner', async () => {
  mount(); await prepareReplacement();
  expect(activity).toHaveBeenLastCalledWith({ dirty: true, pending: false });
  expect(activity.mock.calls.every(([value]) => Object.keys(value).sort().join(',') === 'dirty,pending')).toBe(true);
});
it('freezes authorization target and secret while the separate credential write is pending', async () => {
  mount(); const secret = await prepareReplacement(); api.replaceConnectionCredential.mockReturnValueOnce(new Promise(() => {}));
  fireEvent.click(screen.getByRole('button', { name: 'Store API key' })); await waitFor(() => expect(api.replaceConnectionCredential).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText('Connection needing an API key').disabled).toBe(true); expect(secret.disabled).toBe(true);
  expect(activity).toHaveBeenLastCalledWith({ dirty: true, pending: true });
});
it('a late credential acknowledgement cannot resurrect an unmounted parent workflow via onSaved', async () => {
  const view = mount(); await prepareReplacement(); let finish;
  const completed = new Promise(resolve => { finish = () => resolve({ state: 'auth_stored', provider: 'saved', revision: 'r2', credential: { present: true } }); });
  api.replaceConnectionCredential.mockReturnValueOnce(completed); fireEvent.click(screen.getByRole('button', { name: 'Store API key' }));
  await waitFor(() => expect(api.replaceConnectionCredential).toHaveBeenCalledTimes(2)); view.unmount();
  await act(async () => { finish(); await completed; }); expect(saved).not.toHaveBeenCalled();
});
it.each([{ revision: undefined }, { provider: 'foreign' }, { credential: { present: false } }])(
  'malformed credential receipt cannot publish an own-save revision (%j)', async invalid => {
    mount(); await prepareReplacement();
    api.replaceConnectionCredential.mockResolvedValueOnce({ state: 'auth_stored', provider: 'saved', revision: 'r2', credential: { present: true }, ...invalid });
    fireEvent.click(screen.getByRole('button', { name: 'Store API key' }));
    await screen.findByRole('button', { name: 'Read authorization receipt' });
    expect(saved).not.toHaveBeenCalled();
    expect(api.replaceConnectionCredential).toHaveBeenCalledTimes(2);
  });
