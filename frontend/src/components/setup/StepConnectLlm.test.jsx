// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import StepConnectLlm from './StepConnectLlm.jsx';

const api = vi.hoisted(() => ({ fetchAiPresets: vi.fn(), fetchConnections: vi.fn(), registerConnection: vi.fn(),
  listConnectionModels: vi.fn(), previewConnection: vi.fn(), applyConnection: vi.fn(), readConnection: vi.fn(),
  testConnection: vi.fn(), replaceConnectionCredential: vi.fn(), readConnectionCredential: vi.fn() }));
vi.mock('../../api/setupApi.js', () => ({ fetchAiPresets: api.fetchAiPresets }));
vi.mock('../../api/connectionsApi.js', () => api);
const local = { name: 'local', type: 'openai', base_url: 'http://[::1]:9988/exact/proxy', auth_mode: 'none', api_key_env: 'UNUSED' };
const config = { llm_routing: { providers: [local], default: { provider: 'local', model: 'manual' }, feed: { model: 'exception' } } };
function show(mode, saved = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><StepConnectLlm mode={mode} onSaved={saved} /></QueryClientProvider>);
  return client;
}
beforeEach(() => {
  vi.resetAllMocks();
  api.fetchConnections.mockResolvedValue({ revision: 'r1', config, providers: [local] });
  api.fetchAiPresets.mockResolvedValue({ local_profiles: { profiles: [
    { id: 'light', label: 'Light', provider: local, model: 'qwen3:8b', compatible: true, size_gb: 5.2, compatibility_detail: 'Meets static floors' },
    { id: 'balanced', label: 'Balanced', provider: local, model: 'qwen3:30b', compatible: false, compatibility_detail: 'needs 32 GB memory' },
  ] } });
  api.registerConnection.mockImplementation(async command => {
    const candidate = { ...local, name: 'candidate', base_url: command.base_url };
    const next = { ...config, llm_routing: { ...config.llm_routing, providers: [local, candidate] } };
    api.fetchConnections.mockResolvedValue({ revision: 'r2', config: next, providers: next.llm_routing.providers });
    return { ...command, provider: candidate, state: 'registered_inactive', revision: 'r2' };
  });
  api.listConnectionModels.mockResolvedValue({ catalog: 'returned', models: ['served'], revision: 'r2' });
  api.previewConnection.mockResolvedValue({ revision: 'r2', preview: 'p', ready_to_apply: true, impact: { stages: [] } });
});
afterEach(cleanup);

it.each([undefined, 'local', 'hosted'])('keyless exact IPv6 path remains mode-independent (%s), with explicit consent', async mode => {
  const client = show(mode);
  fireEvent.click(await screen.findByRole('button', { name: 'Change All steps', exact: true }));
  expect(screen.getByLabelText('API address').value).toBe(local.base_url);
  expect(document.querySelector('input[type="password"]')).toBeNull();
  expect(api.registerConnection).not.toHaveBeenCalled();
  expect(screen.queryByLabelText(/I approve/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
  await screen.findByRole('combobox', { name: 'Model' });
  expect(api.registerConnection.mock.calls[0][0]).toMatchObject({ source: 'local', auth: 'none', base_url: local.base_url });
  expect(api.testConnection).not.toHaveBeenCalled();
  client.clear();
});

it('profile suggestions only prepare a draft; incompatible suggestions cannot change any saved route', async () => {
  const saved = vi.fn(), client = show('local', saved);
  const light = await screen.findByRole('button', { name: 'Light' });
  expect(screen.getByRole('button', { name: 'Balanced' }).disabled).toBe(true);
  fireEvent.click(light);
  expect(screen.getByLabelText('API address').value).toBe(local.base_url);
  expect(screen.getByRole('button', { name: 'Connect provider' }).disabled).toBe(false);
  expect(api.registerConnection).not.toHaveBeenCalled();
  expect(api.applyConnection).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled();
  expect(config.llm_routing.feed).toEqual({ model: 'exception' });
  client.clear();
});

it('manual model editing survives catalog failure without activation or an inference claim', async () => {
  api.listConnectionModels.mockResolvedValue({ catalog: 'unavailable', models: [], revision: 'r2', reason_code: 'catalog_http_error',
    http_status: 404, detail: 'Server returned HTTP 404. Review the exact base and protocol.' });
  const client = show('local');
  fireEvent.click(await screen.findByRole('button', { name: 'Change All steps', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
  expect(await screen.findByText(/Server returned HTTP 404/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'manual-model' } });
  expect(screen.getByLabelText('Model').value).toBe('manual-model');
  await screen.findByRole('button', { name: 'Use as default' });
  expect(api.applyConnection).not.toHaveBeenCalled();
  expect(api.testConnection).not.toHaveBeenCalled();
  client.clear();
});

it.each([422, 403, 503, undefined])('separate credential failure %s invalidates health and cannot be blindly retried', async status => {
  const hosted = { ...local, name: 'hosted', base_url: 'https://example.invalid/exact', auth_mode: 'legacy' };
  api.fetchConnections.mockResolvedValue({ revision: 'r1', providers: [hosted], config: { llm_routing: { providers: [hosted], default: { provider: 'hosted', model: 'served' } } } });
  api.replaceConnectionCredential.mockResolvedValueOnce({ operation_id: 'op', revision: 'r1', preview: 'preview', impact: { consumers: ['default'] } });
  api.replaceConnectionCredential.mockRejectedValueOnce(Object.assign(new Error('Credential save failed'), { status }));
  const client = show('hosted');
  client.setQueryData(['setup-doctor'], { ready: true });
  fireEvent.click(await screen.findByText('Manage saved API keys'));
  fireEvent.change(screen.getByLabelText('Connection to update'), { target: { value: 'hosted' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review authorization impact' }));
  fireEvent.change(await screen.findByLabelText('New API key'), { target: { value: 'synthetic' } });
  fireEvent.click(screen.getByRole('button', { name: 'Replace saved API key' }));
  expect(await screen.findByText(/Credential save failed/)).toBeTruthy();
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
  expect(api.replaceConnectionCredential).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('button', { name: 'Review authorization impact' }).disabled).toBe(true);
  expect(api.listConnectionModels).not.toHaveBeenCalled();
  client.clear();
});

it('credential readback owns model editing until its exact new revision is adopted', async () => {
  const provider = { name: 'candidate', type: 'anthropic', base_url: null };
  let operation, finishRead;
  api.fetchConnections.mockResolvedValue({ revision: 'r1', providers: [], config: null });
  api.registerConnection.mockImplementation(async command => {
    operation = command.operation_id;
    return { operation_id: operation, revision: 'r1', state: 'draft', bootstrap: true, authorization_new: true, provider };
  });
  api.listConnectionModels.mockResolvedValue({ revision: 'r1', catalog: 'unavailable', models: [] });
  api.replaceConnectionCredential.mockResolvedValueOnce({ operation_id: 'credential', revision: 'r1', preview: 'auth-preview', impact: { consumers: [] } });
  api.replaceConnectionCredential.mockResolvedValueOnce({ state: 'auth_stored', provider: 'candidate', credential: { present: true }, revision: 'r2' });
  api.readConnection.mockImplementation(() => new Promise(resolve => { finishRead = resolve; }));
  api.previewConnection.mockResolvedValue({ revision: 'r2', preview: 'preview-r2', ready_to_apply: true,
    impact: { stages: [], shared_authorization: {}, telegram: { selection: null } } });
  const client = show('hosted');
  fireEvent.click(await screen.findByRole('button', { name: 'Add provider' }));
  fireEvent.click(screen.getByText('Server address, API type and access'));
  fireEvent.change(screen.getByLabelText('Authorization'), { target: { value: 'new' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  fireEvent.click(await screen.findByText('Add an API key for this connection'));
  fireEvent.click(screen.getByRole('button', { name: 'Review authorization impact' }));
  fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'synthetic' } });
  fireEvent.click(screen.getByRole('button', { name: 'Store API key' }));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText('Model').disabled).toBe(true);
  api.fetchConnections.mockResolvedValue({ revision: 'r2', providers: [], config: null });
  await act(async () => finishRead({ operation_id: operation, provider, revision: 'r2', current_revision: 'r2',
    provider_matches: false, selection_matches: false, bootstrap: true, auth: 'stored' }));
  await waitFor(() => expect(screen.getByLabelText('Model').disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'synthetic' } });
  await waitFor(() => expect(api.previewConnection).toHaveBeenCalledWith(expect.objectContaining({ revision: 'r2' })));
  await screen.findByRole('button', { name: 'Use as default' });
  client.clear();
});

it('empty-registry external entry offers new authorization, not a no-auth-only apparent flow', async () => {
  api.fetchConnections.mockResolvedValue({ revision: 'r1', providers: [], config: null });
  api.registerConnection.mockImplementation(async command => ({ operation_id: command.operation_id, revision: 'r1', state: 'draft', bootstrap: true,
    authorization_new: true, provider: { name: 'candidate', type: command.type, base_url: command.base_url, auth_mode: 'legacy' } }));
  const client = show('hosted');
  fireEvent.click(await screen.findByRole('button', { name: 'Add provider' }));
  fireEvent.change(screen.getByLabelText('API address'), { target: { value: 'https://example.invalid/native' } });
  fireEvent.click(screen.getByText('Server address, API type and access'));
  fireEvent.change(screen.getByLabelText('Authorization'), { target: { value: 'new' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await waitFor(() => expect(api.registerConnection).toHaveBeenCalledTimes(1));
  expect(api.registerConnection.mock.calls[0][0]).toMatchObject({ auth: 'new', approved: true });
  expect(await screen.findByText('Add an API key for this connection')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Review authorization impact' }).disabled).toBe(false);
  expect(api.applyConnection).not.toHaveBeenCalled();
  client.clear();
});
