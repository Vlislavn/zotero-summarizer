// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { renderRouter } from '../testRouter.jsx';
const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn() }));
const conn = vi.hoisted(() => ({ fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(), previewConnection: vi.fn(), applyConnection: vi.fn(), testConnection: vi.fn(), replaceConnectionCredential: vi.fn(), readConnectionCredential: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/connectionsApi.js', async importOriginal => ({ ...await importOriginal(), ...conn }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
import Settings from './Settings.jsx';
import ConfigTask from '../components/settings/ConfigTask.jsx';
let client;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return renderRouter(<Routes><Route path="/settings" element={<Settings />} /><Route path="/today" element={<ConfigTask task="selection" />} /></Routes>, client, ['/today?task=selection']);
}
afterEach(() => { cleanup(); client?.clear(); vi.restoreAllMocks(); vi.resetAllMocks(); });
it('separate selection and connection tasks admit a draft exit explicitly and never overwrite the other owner', async () => {
  let config = { _revision: 'r1', llm_enabled: true, research_goals: ['Saved research'], triage_criteria: ['Evidence'],
    university_access: { login_url: 'https://saved.invalid/login' },
    llm_routing: { providers: [{ name: 'old', type: 'openai', base_url: 'http://127.0.0.1:9999/proxy', auth_mode: 'none' }],
      default: { provider: 'old', model: 'old-model' }, feed: { model: 'exception' } } };
  api.fetchConfig.mockImplementation(async () => config);
  api.updateConfig.mockImplementation(async payload => ({ status: 'ok', config: { ...payload, _revision: 'r4' } }));
  conn.fetchConnections.mockImplementation(async () => ({ revision: config._revision, config, providers: config.llm_routing.providers }));
  conn.registerConnection.mockImplementation(async command => {
    const provider = { name: 'new', type: command.type, base_url: command.base_url, auth_mode: 'none' };
    config = { ...config, _revision: 'r2', llm_routing: { ...config.llm_routing, providers: [...config.llm_routing.providers, provider] } };
    return { operation_id: command.operation_id, provider, revision: 'r2', state: 'registered_inactive' };
  });
  conn.listConnectionModels.mockResolvedValue({ catalog: 'returned', models: ['new-model'], revision: 'r2' });
  conn.previewConnection.mockResolvedValue({ revision: 'r2', preview: 'preview', ready_to_apply: true,
    impact: { stages: [], shared_authorization: { providers: [], consumers: [] }, telegram: { selection: null } } });
  conn.applyConnection.mockImplementation(async () => {
    config = { ...config, _revision: 'r3', llm_routing: { ...config.llm_routing, default: { provider: 'new', model: 'new-model' } } };
    return { state: 'selection_saved', selection_matches: true, provider: { name: 'new' }, revision: 'r3', config };
  });
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  const { router } = mount();
  fireEvent.change(await screen.findByLabelText('Goal 1'), { target: { value: 'Unsaved research' } });
  await act(async () => router.navigate('/settings?task=connection'));
  expect(screen.getByLabelText('Goal 1').value).toBe('Unsaved research'); expect(conn.fetchConnections).not.toHaveBeenCalled();
  window.confirm.mockReturnValue(true); await act(async () => router.navigate('/settings?task=connection'));
  await screen.findByRole('region', { name: 'Model for each step' });
  fireEvent.click(screen.getByRole('button', { name: 'Change Fallback (steps without their own model)' }));
  await screen.findByLabelText('API address');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Add provider' })); fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await screen.findByLabelText('Model'); expect(config.research_goals).toEqual(['Saved research']); expect(api.updateConfig).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'new-model' } });
  fireEvent.click(await screen.findByRole('button', { name: 'Use as default' })); await screen.findByText(/Selection saved/);
  expect(config.research_goals).toEqual(['Saved research']); expect(config.university_access.login_url).toBe('https://saved.invalid/login');
  await act(async () => router.navigate('/today?task=selection'));
  const goals = await screen.findByLabelText('Goal 1'); expect(goals.value).toBe('Saved research');
  fireEvent.change(goals, { target: { value: 'New research' } }); fireEvent.click(screen.getByRole('button', { name: 'Save article selection' }));
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1)); const payload = api.updateConfig.mock.calls[0][0];
  expect(payload._revision).toBe('r3'); expect(payload.llm_routing.default).toEqual({ provider: 'new', model: 'new-model' });
  expect(payload.llm_routing.feed).toEqual({ model: 'exception' }); expect(payload.research_goals).toEqual(['New research']);
  expect(payload.university_access.login_url).toBe('https://saved.invalid/login');
});
it('a background config read cannot put a fresh revision on an old dirty draft', async () => {
  const original = { _revision: 'r1', llm_enabled: false, research_goals: ['Saved'], triage_criteria: ['Evidence'],
    llm_routing: { providers: [], default: { provider: 'old', model: 'old-model' } } };
  api.fetchConfig.mockResolvedValue(original); api.updateConfig.mockRejectedValue(Object.assign(new Error('Revision changed'), { status: 409 }));
  mount(); fireEvent.change(await screen.findByLabelText('Goal 1'), { target: { value: 'Local draft' } });
  await act(async () => client.setQueryData(['runtime-config'], { ...original, _revision: 'r2',
    llm_routing: { ...original.llm_routing, default: { provider: 'new', model: 'new-model' } } }));
  fireEvent.click(screen.getByRole('button', { name: 'Save article selection' })); await screen.findByRole('alert');
  expect(api.updateConfig).toHaveBeenCalledTimes(1); expect(api.updateConfig.mock.calls[0][0]._revision).toBe('r1');
  expect(api.updateConfig.mock.calls[0][0].research_goals).toEqual(['Local draft']); expect(screen.getByLabelText('Goal 1').value).toBe('Local draft');
});
function prepareSaved(auth = 'none') {
  const config = { _revision: 'r1', llm_enabled: false, output_language: 'English', research_goals: ['Saved'], triage_criteria: ['Evidence'],
    llm_routing: { providers: [{ name: 'saved', type: 'openai', base_url: 'http://127.0.0.1:9999/v1', auth_mode: auth, api_key_env: 'SYNTHETIC_KEY' }], default: { provider: 'saved', model: 'saved-model' } } };
  api.fetchConfig.mockResolvedValue(config); conn.fetchConnections.mockResolvedValue({ config, providers: config.llm_routing.providers, revision: 'r1' });
  vi.spyOn(window, 'confirm').mockReturnValue(false); return config;
}
async function openConnection() {
  const scene = mount(); await screen.findByLabelText('Goal 1');
  await act(async () => scene.router.navigate('/settings?task=connection'));
  await screen.findByRole('region', { name: 'Model for each step' });
  fireEvent.click(screen.getByRole('button', { name: 'Change Fallback (steps without their own model)' }));
  fireEvent.click(screen.getByText('Server address, API type and access'));
  await screen.findByLabelText('API address'); return scene;
}
it('a pristine saved-connection editor leaves cleanly, rather than treating editor visibility as unsaved work', async () => {
  prepareSaved(); const { router } = await openConnection(); await act(async () => router.navigate('/today?task=selection'));
  expect(router.state.location.pathname).toBe('/today'); expect(window.confirm).not.toHaveBeenCalled();
});
it('a pending saved-model Test remains protected after closing the connection form', async () => {
  prepareSaved(); conn.testConnection.mockReturnValue(new Promise(() => {})); const { router } = await openConnection();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await waitFor(() => expect(screen.queryByLabelText('API address')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Test saved model' })); await waitFor(() => expect(conn.testConnection).toHaveBeenCalledTimes(1));
  await act(async () => router.navigate('/today?task=selection')); expect(router.state.location.pathname).toBe('/settings'); expect(window.confirm).toHaveBeenCalledTimes(1);
});
it('matching connection aliases protect both a dirty address and pending-only Test ownership', async () => {
  prepareSaved(); const { router } = await openConnection();
  const field = screen.getByLabelText('API address');
  fireEvent.change(field, { target: { value: 'http://127.0.0.1:9998/retained-draft' } });
  await act(async () => router.navigate('/settings#ai-models'));
  expect(screen.getByLabelText('API address')).toBe(field);
  expect(field.value).toBe('http://127.0.0.1:9998/retained-draft');
  expect(window.confirm).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  conn.testConnection.mockReturnValue(new Promise(() => {}));
  fireEvent.click(screen.getByRole('button', { name: 'Test saved model' }));
  await waitFor(() => expect(conn.testConnection).toHaveBeenCalledTimes(1));
  await act(async () => router.navigate('/settings#ai-models'));
  expect(router.state.location.search).toBe('?task=connection');
  expect(window.confirm).toHaveBeenCalledTimes(2);
  expect(conn.fetchConnections).toHaveBeenCalledTimes(1);
  expect(api.updateConfig).not.toHaveBeenCalled();
});
it('a separate authorization draft also belongs to the one navigation guard and retains its secret locally on cancelled leave', async () => {
  prepareSaved('auto'); conn.replaceConnectionCredential.mockResolvedValue({ preview: 'preview', revision: 'r1', operation_id: 'synthetic-op', impact: { consumers: [] } });
  const { router } = await openConnection(); fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByText('Manage saved API keys'));
  fireEvent.change(screen.getByLabelText('Connection to update'), { target: { value: 'saved' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review authorization impact' })); const secret = await screen.findByLabelText('New API key');
  fireEvent.change(secret, { target: { value: 'synthetic-not-a-real-credential' } });
  await act(async () => router.navigate('/today?task=selection')); expect(router.state.location.pathname).toBe('/settings');
  expect(secret.value).toBe('synthetic-not-a-real-credential'); expect(conn.replaceConnectionCredential).toHaveBeenCalledTimes(1); expect(api.updateConfig).not.toHaveBeenCalled();
});
it.each(['runtime-config', 'llm-connections'])('held publication cannot overwrite a conflicting snapshot learned through %s', async signal => {
  let server = prepareSaved(); let hold = false; let release;
  api.fetchConfig.mockImplementation(async () => server);
  conn.fetchConnections.mockImplementation(async () => {
    const snapshot = { revision: server._revision, config: server, providers: server.llm_routing.providers };
    if (hold) { hold = false; return new Promise(resolve => { release = () => resolve(snapshot); }); }
    return snapshot;
  });
  conn.registerConnection.mockImplementation(async command => {
    const provider = { name: 'new', type: command.type, base_url: command.base_url, auth_mode: 'none' };
    server = { ...server, _revision: 'r2', llm_routing: { ...server.llm_routing, providers: [...server.llm_routing.providers, provider] } }; hold = true;
    return { operation_id: command.operation_id, provider, revision: 'r2', state: 'registered_inactive' };
  });
  conn.listConnectionModels.mockResolvedValue({ revision: 'r2', catalog: 'returned', models: ['new-model'] });
  await openConnection(); fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add provider' })); fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await waitFor(() => expect(release).toBeTypeOf('function'));
  server = { ...server, _revision: 'r9', output_language: 'Newer saved language' };
  await act(async () => client.refetchQueries({ queryKey: [signal] }));
  if (signal === 'llm-connections') {
    expect(client.getQueryData(['llm-connections']).revision).toBe('r9');
    expect(client.getQueryData(['runtime-config'])._revision).toBe('r1');
  } else expect(client.getQueryData(['runtime-config'])._revision).toBe('r9');
  const connectionRevisions = [], runtimeRevisions = [];
  const unsubscribe = client.getQueryCache().subscribe(event => {
    if (event.query.queryKey[0] === 'llm-connections') connectionRevisions.push(event.query.state.data?.revision);
    if (event.query.queryKey[0] === 'runtime-config') runtimeRevisions.push(event.query.state.data?._revision);
  });
  await act(async () => release());
  unsubscribe();
  expect(connectionRevisions).not.toContain('r2');
  expect(runtimeRevisions).not.toContain('r2');
  await waitFor(() => expect(client.getQueryData(['runtime-config'])._revision).toBe('r9'));
  expect(client.getQueryData(['llm-connections']).revision).toBe('r9');
  await screen.findByText('Saved settings changed. Start a fresh connection approval. Earlier writes are not undone; no request was replayed.');
  expect(client.getQueryData(['runtime-config']).output_language).toBe('Newer saved language');
  expect(screen.queryByText(/Works — answered/)).toBeNull();
  expect(conn.listConnectionModels).not.toHaveBeenCalled();
  expect(api.updateConfig).not.toHaveBeenCalled();
});

async function repairAuthorization({ lostAck = false, foreign = null } = {}) {
  let server = prepareSaved('legacy');
  const outcome = { state: 'auth_stored', provider: 'saved', credential: { present: true }, revision: 'r2' };
  let release;
  api.fetchConfig.mockImplementation(async () => server);
  conn.fetchConnections.mockImplementation(async () => ({ config: server, providers: server.llm_routing.providers, revision: server._revision }));
  conn.replaceConnectionCredential.mockImplementation(async command => {
    if (!command.secret) return { preview: 'preview', revision: command.revision, operation_id: command.operation_id, impact: { consumers: [] } };
    server = { ...server, _revision: 'r2' };
    if (foreign) return new Promise(resolve => { release = () => resolve(outcome); });
    if (lostAck) throw new Error('Lost credential acknowledgement');
    return outcome;
  });
  conn.readConnectionCredential.mockResolvedValue(outcome);
  conn.registerConnection.mockImplementation(async command => ({ operation_id: command.operation_id, provider: server.llm_routing.providers[0], revision: server._revision, state: 'registered_inactive' }));
  conn.listConnectionModels.mockResolvedValue({ revision: 'r2', catalog: 'returned', models: ['returned-model'] });
  await openConnection();
  fireEvent.click(screen.getByText('Manage saved API keys'));
  fireEvent.change(screen.getByLabelText('Connection to update'), { target: { value: 'saved' } });
  fireEvent.click(screen.getByRole('button', { name: 'Review authorization impact' }));
  fireEvent.change(await screen.findByLabelText('New API key'), { target: { value: 'synthetic-not-a-real-key' } });
  fireEvent.click(screen.getByRole('button', { name: 'Replace saved API key' }));
  if (foreign) {
    await waitFor(() => expect(release).toBeTypeOf('function'));
    if (foreign === 'runtime-config') await act(async () => client.setQueryData(['runtime-config'], { ...server, _revision: 'r9' }));
    if (foreign === 'llm-connections') await act(async () => client.setQueryData(['llm-connections'], { config: server, providers: server.llm_routing.providers, revision: 'r9' }));
    if (foreign !== 'fresh-GET') conn.fetchConnections.mockResolvedValueOnce({ config: server, providers: server.llm_routing.providers, revision: 'r2' });
    server = { ...server, _revision: 'r9', output_language: 'Concurrent language' };
    await act(async () => release());
  } else if (lostAck) {
    fireEvent.click(await screen.findByRole('button', { name: 'Read authorization receipt' }));
  }
  await waitFor(() => expect(screen.getByRole('button', { name: 'Review authorization impact' }).disabled).toBe(false));
  return server;
}
it.each([false, true])('own credential receipt coordinates both caches before Connect without reload (lost ACK=%s)', async lostAck => {
  const server = await repairAuthorization({ lostAck });
  await waitFor(() => expect(client.getQueryData(['runtime-config'])._revision).toBe('r2'));
  expect(client.getQueryData(['llm-connections']).revision).toBe('r2');
  expect(client.getQueryData(['runtime-config']).llm_routing).toEqual(server.llm_routing);
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
  await screen.findByRole('option', { name: 'returned-model' });
  expect(conn.registerConnection.mock.lastCall[0].revision).toBe('r2');
  expect(conn.listConnectionModels).toHaveBeenCalledTimes(1);
  expect(conn.replaceConnectionCredential).toHaveBeenCalledTimes(2);
  expect(conn.readConnectionCredential).toHaveBeenCalledTimes(lostAck ? 1 : 0);
  expect(screen.queryByText(/Saved settings changed/)).toBeNull();
  expect(api.updateConfig).not.toHaveBeenCalled();
});
it.each(['runtime-config', 'llm-connections', 'fresh-GET'])('credential receipt does not authorize a foreign %s revision', async foreign => {
  await repairAuthorization({ foreign });
  await screen.findByText(/Saved settings changed. Start a fresh connection approval/);
  await waitFor(() => expect(client.getQueryData(['runtime-config'])._revision).toBe('r9'));
  expect(client.getQueryData(['runtime-config']).output_language).toBe('Concurrent language');
  expect(conn.registerConnection).not.toHaveBeenCalled();
  expect(conn.listConnectionModels).not.toHaveBeenCalled();
  expect(conn.replaceConnectionCredential).toHaveBeenCalledTimes(2);
  expect(api.updateConfig).not.toHaveBeenCalled();
});
