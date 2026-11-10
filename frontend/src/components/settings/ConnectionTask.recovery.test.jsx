// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import ConnectionTask from './ConnectionTask.jsx';
const api = vi.hoisted(() => ({ fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(), previewConnection: vi.fn(), applyConnection: vi.fn(), readConnection: vi.fn() }));
vi.mock('../../api/connectionsApi.js', () => api);
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const provider = { name: 'new', type: 'openai', base_url: 'http://localhost:11434/v1', auth_mode: 'none' };
const cfg = { llm_routing: { providers: [], default: { model: 'old' } } };
function show(config = cfg) {
  api.fetchConnections.mockResolvedValue({ revision: 'r1', config, providers: [] });
  const saved = vi.fn(), client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConnectionTask onSaved={saved} /></QueryClientProvider>);
  return { saved, client };
}
async function connect() {
  fireEvent.click(await screen.findByRole('button', { name: 'Add provider' }));
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
}
function registration() {
  api.registerConnection.mockImplementation(async command => ({ operation_id: command.operation_id, state: 'registered_inactive', provider, revision: 'r1' }));
  api.listConnectionModels.mockResolvedValue({ catalog: 'unavailable', models: [], revision: 'r1' });
}
it('requires complete crossed-route choices and automatically refreshes before Use; no keep escape', async () => {
  show(); registration();
  const impact = { stages: [], required_corrections: ['feed', 'backlog'], shared_authorization: {}, telegram: { selection: null } };
  api.previewConnection.mockImplementation(async command => ({ revision: 'r1', preview: JSON.stringify(command.corrections),
    ready_to_apply: Boolean(command.corrections.feed && command.corrections.backlog), impact }));
  await connect(); fireEvent.change(await screen.findByLabelText('Model'), { target: { value: 'manual' } });
  await screen.findByLabelText('New paper screening');
  expect(screen.queryByRole('button', { name: 'Use as default' })).toBeNull();
  expect(screen.queryByRole('option', { name: /unverified/ })).toBeNull();
  fireEvent.change(screen.getByLabelText('New paper screening'), { target: { value: 'previous' } });
  fireEvent.change(screen.getByLabelText('AI backlog'), { target: { value: 'default' } });
  await screen.findByRole('button', { name: 'Use as default' });
  expect(api.previewConnection.mock.lastCall[0].corrections).toEqual({ feed: 'previous', backlog: 'default' });
  expect(api.applyConnection).not.toHaveBeenCalled();
});
it('bootstrap registration keeps manual entry mounted without publishing invented parent config', async () => {
  const { saved } = show(null);
  api.registerConnection.mockImplementation(async command => ({ operation_id: command.operation_id, state: 'draft', provider, revision: 'r1', bootstrap: true }));
  api.listConnectionModels.mockResolvedValue({ catalog: 'unavailable', models: [], revision: 'r1' });
  await connect(); expect(await screen.findByLabelText('Model')).toBeTruthy();
  expect(saved).not.toHaveBeenCalled(); expect(api.applyConnection).not.toHaveBeenCalled();
});
it('retired held impact cannot publish old answers; latest edit gets its own fresh preview', async () => {
  show(); registration(); let complete;
  api.previewConnection.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  api.previewConnection.mockResolvedValue({ revision: 'r1', preview: 'new', ready_to_apply: false, impact: { stages: [] } });
  await connect(); fireEvent.change(await screen.findByLabelText('Model'), { target: { value: 'first' } });
  await waitFor(() => expect(api.previewConnection).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'second' } });
  await act(async () => complete({ revision: 'r1', preview: 'obsolete', ready_to_apply: true, impact: { stages: [] } }));
  await waitFor(() => expect(api.previewConnection.mock.lastCall[0].model).toBe('second'));
  expect(screen.queryByRole('button', { name: 'Use as default' })).toBeNull(); expect(api.applyConnection).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Model').value).toBe('second');
});
it.each(['r1', 'r2'])('lost registration ACK retains its originating revision for readback at %s, without replay', async revision => {
  const { saved, client } = show(); let operation;
  api.registerConnection.mockImplementation(async command => { operation = command.operation_id; throw new Error('Lost registration ACK'); });
  await connect(); const readback = await screen.findByRole('button', { name: 'Read back intended connection' });
  api.readConnection.mockResolvedValue({ operation_id: operation, provider, state: 'registered_inactive', revision, current_revision: revision, provider_matches: true, selection_matches: false });
  api.fetchConnections.mockResolvedValue({ revision, config: cfg, providers: [provider] });
  fireEvent.click(readback); expect(await screen.findByText(/Readback: exact server registered/)).toBeTruthy();
  expect(saved).toHaveBeenCalledWith({ ...cfg, _revision: revision }, { previousRevision: 'r1' });
  expect(client.getQueryData(['llm-connections']).revision).toBe(revision);
  expect(api.readConnection).toHaveBeenCalledTimes(1); expect(api.registerConnection).toHaveBeenCalledTimes(1);
  expect(api.listConnectionModels).not.toHaveBeenCalled(); expect(api.applyConnection).not.toHaveBeenCalled();
  client.clear();
});
it('preview refusal is not automatically retried', async () => {
  show(); registration(); api.previewConnection.mockRejectedValue(new Error('configuration changed'));
  await connect(); fireEvent.change(await screen.findByLabelText('Model'), { target: { value: 'manual' } });
  await screen.findByText('configuration changed');
  expect(api.previewConnection).toHaveBeenCalledTimes(1); expect(api.applyConnection).not.toHaveBeenCalled();
});
