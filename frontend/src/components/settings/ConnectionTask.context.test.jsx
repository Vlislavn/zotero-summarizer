// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import ConnectionTask from './ConnectionTask.jsx';

const api = vi.hoisted(() => ({ fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(),
  previewConnection: vi.fn(), applyConnection: vi.fn(), readConnection: vi.fn(), testConnection: vi.fn() }));
vi.mock('../../api/connectionsApi.js', () => api);
const clients = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.resetAllMocks(); });
const provider = { name: 'saved', type: 'openai', base_url: 'https://example.invalid/v1', auth_mode: 'none' };
const config = { llm_routing: { providers: [provider], default: { provider: provider.name, model: 'saved-model' } } };
const view = revision => ({ revision, config, providers: [provider] });
const observation = revision => ({ revision, model: 'saved-model', status: 'operational', current: true,
  attempted: true, ownership: 'completed', response: 'Synthetic response', request_completion: { successful: true } });
function show(initial = view('r1')) {
  api.fetchConnections.mockResolvedValue(initial);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><ConnectionTask /></QueryClientProvider>);
  return client;
}
async function refetch(client, revision) {
  api.fetchConnections.mockResolvedValue(view(revision));
  await act(async () => client.refetchQueries({ queryKey: ['llm-connections'] }));
}

it('marks a completed observation stale after a credential/configuration revision refetch', async () => {
  const client = show(); api.testConnection.mockResolvedValue(observation('r1'));
  fireEvent.click(await screen.findByRole('button', { name: 'Test saved model' }));
  await screen.findByText(/Works — answered/);
  await refetch(client, 'r2');
  expect(await screen.findByText(/Test belongs to an older saved selection/)).toBeTruthy();
  expect(screen.queryByText(/Works — answered/)).toBeNull();
  expect(screen.queryByText('Synthetic response')).toBeNull();
  expect(api.testConnection).toHaveBeenCalledTimes(1);
});

it('does not present a held old success as current after learning a newer revision', async () => {
  const client = show(); let finish;
  api.testConnection.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(await screen.findByRole('button', { name: 'Test saved model' }));
  await waitFor(() => expect(api.testConnection).toHaveBeenCalledTimes(1));
  await refetch(client, 'r2');
  await act(async () => finish(observation('r1')));
  expect(await screen.findByText(/Test belongs to an older saved selection/)).toBeTruthy();
  expect(screen.queryByText('Synthetic response')).toBeNull();
  expect(api.testConnection).toHaveBeenCalledTimes(1);
  api.testConnection.mockResolvedValue({ ...observation('r2'), response: 'Fresh synthetic response' });
  const button = screen.getByRole('button', { name: 'Test saved model' });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  expect(await screen.findByText('Fresh synthetic response')).toBeTruthy();
  expect(api.testConnection.mock.lastCall[0]).toEqual({ revision: 'r2', model: 'saved-model' });
});

async function connectCatalog(bootstrap, catalog) {
  show(bootstrap ? { revision: 'r1', config: null, providers: [] } : view('r1'));
  api.registerConnection.mockImplementation(async command => ({ operation_id: command.operation_id, provider,
    revision: 'r1', state: bootstrap ? 'draft' : 'registered_inactive', bootstrap }));
  api.listConnectionModels.mockResolvedValue({ catalog: 'unavailable', models: [], revision: 'r1',
    state: bootstrap ? 'draft' : 'registered_inactive', bootstrap, ...catalog });
  fireEvent.click(await screen.findByRole('button', { name: 'Add provider' }));
  fireEvent.change(screen.getByLabelText('API address'), { target: { value: provider.base_url } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await screen.findByLabelText('Model');
}

it('explains deferred bootstrap discovery and does not offer an ineffective retry', async () => {
  const reason = 'First remote connection: enter a manual model ID and explicitly save a complete default before metadata discovery.';
  await connectCatalog(true, { reason });
  expect(screen.getByText(reason)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Retry model catalog' })).toBeNull();
  expect(screen.getByLabelText('Model')).toBeTruthy();
  expect(api.listConnectionModels).toHaveBeenCalledTimes(1);
  expect(api.applyConnection).not.toHaveBeenCalled();
});

it('retains explicit catalog retry for an actual ordinary metadata failure', async () => {
  const detail = 'Server returned HTTP 401. Review authorization for this exact server.';
  await connectCatalog(false, { reason_code: 'catalog_http_error', http_status: 401, detail });
  expect(screen.getByText(detail)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Retry model catalog' })).toBeTruthy();
  expect(api.listConnectionModels).toHaveBeenCalledTimes(1);
});
