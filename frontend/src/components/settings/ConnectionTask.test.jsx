// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import ConnectionTask from './ConnectionTask.jsx';

const api = vi.hoisted(() => ({ fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(),
  previewConnection: vi.fn(), applyConnection: vi.fn(), readConnection: vi.fn(), testConnection: vi.fn() }));
vi.mock('../../api/connectionsApi.js', () => api);
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const config = { llm_routing: { providers: [{ name: 'old', type: 'openai', base_url: 'http://[::1]:9000/proxy', api_key_env: 'HIDDEN' }],
  default: { provider: 'old', model: 'old-model' }, feed: { model: 'exception' } } };
function show(extra = {}) {
  api.fetchConnections.mockResolvedValue({ revision: 'r1', config, providers: config.llm_routing.providers });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConnectionTask {...extra} /></QueryClientProvider>);
  return client;
}
it('offers one explicit saved-model test with concise UNKNOWN, optional evidence and direct repair', async () => {
  show();
  api.testConnection.mockResolvedValue({ revision: 'r1', current: true, status: 'fail', attempted: false, hardware_safe: 'unknown',
    reason_code: 'effective_envelope_unknown', detail: 'Technical local envelope evidence; no inference submitted.' });
  fireEvent.click(await screen.findByRole('button', { name: 'Test saved model' }));
  expect(await screen.findByText('Test not sent')).toBeTruthy();
  expect(screen.getByText(/Technical local envelope/)).toBeTruthy();
  expect(api.testConnection).toHaveBeenCalledWith({ revision: 'r1', model: 'old-model' });
  expect(api.registerConnection).not.toHaveBeenCalled(); expect(api.listConnectionModels).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Change Fallback (steps without their own model)' }));
  expect(screen.getByLabelText('API address').value).toBe('http://[::1]:9000/proxy');
});
it('keeps the separately explicit saved-model test available when recovery opens the editor', async () => {
  show({ autoOpen: true }); await screen.findByLabelText('API address');
  expect(screen.getByRole('button', { name: 'Test saved model' })).toBeTruthy();
  expect(api.testConnection).not.toHaveBeenCalled(); expect(api.registerConnection).not.toHaveBeenCalled();
});
it('starts with saved summary; ordinary address entry makes rare choices contextual', async () => {
  show(); await screen.findByRole('region', { name: 'Model for each step' });
  expect(screen.queryByLabelText(/Credential name|API key/)).toBeNull(); expect(screen.queryByLabelText('API address')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));
  expect(screen.getByLabelText('API protocol').closest('details').open).toBe(false);
  expect(screen.queryByLabelText(/I approve/)).toBeNull();
  expect(screen.getByText(/Save an unused connection and fetch model names/).textContent).toContain('with no key');
  expect(api.registerConnection).not.toHaveBeenCalled(); expect(api.listConnectionModels).not.toHaveBeenCalled();
});
it('manual fallback automatically previews, never applies; confirmed Use returns to summary', async () => {
  const saved = vi.fn(), client = show({ onSaved: saved }); await screen.findByRole('region', { name: 'Model for each step' });
  fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));
  fireEvent.change(screen.getByLabelText('API address'), { target: { value: 'http://127.0.0.1:9911/exact' } });
  let registered;
  api.registerConnection.mockImplementation(async command => {
    registered = { name: 'new', type: 'openai', base_url: command.base_url, auth_mode: 'none' };
    const next = { ...config, llm_routing: { ...config.llm_routing, providers: [...config.llm_routing.providers, registered] } };
    api.fetchConnections.mockResolvedValue({ revision: 'r2', config: next, providers: next.llm_routing.providers });
    return { operation_id: command.operation_id, state: 'registered_inactive', provider: registered, revision: 'r2' };
  });
  api.listConnectionModels.mockResolvedValue({ catalog: 'unavailable', models: [], revision: 'r2' });
  api.previewConnection.mockResolvedValue({ revision: 'r2', preview: 'p', ready_to_apply: true, impact: { stages: [
    { stage: 'feed', before: { provider: 'old', model: 'exception' }, after: { provider: 'new', model: 'exception' } },
  ], shared_authorization: { providers: [], consumers: [] }, telegram: { selection: null } } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await screen.findByLabelText('Model');
  expect(api.registerConnection.mock.calls[0][0]).toMatchObject({ approved: true, revision: 'r1' });
  expect(screen.getByText(/No key is used/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'manual-model' } });
  const impact = await screen.findByRole('region', { name: 'Connection impact' });
  expect(within(impact).getByText('New paper screening').parentElement.textContent).toContain('exception → exception');
  expect(screen.queryByRole('button', { name: 'Review impact' })).toBeNull(); expect(api.applyConnection).not.toHaveBeenCalled();
  api.applyConnection.mockImplementation(async () => {
    const next = { ...config, llm_routing: { ...config.llm_routing, providers: [...config.llm_routing.providers, registered],
      default: { provider: 'new', model: 'manual-model' } } };
    api.fetchConnections.mockResolvedValue({ revision: 'r3', config: next, providers: next.llm_routing.providers });
    return { state: 'selection_saved', selection_matches: true, revision: 'r3', provider: registered, config: next };
  });
  fireEvent.click(await screen.findByRole('button', { name: 'Use as default' }));
  await screen.findByText(/Selection saved/);
  expect(screen.queryByLabelText('Model')).toBeNull(); expect(screen.getByRole('button', { name: 'Change Fallback (steps without their own model)', exact: true })).toBeTruthy();
  expect(api.applyConnection).toHaveBeenCalledTimes(1); client.clear();
});
it('a late catalog cannot overwrite a fresh address draft or activate anything', async () => {
  let finish; show(); await screen.findByRole('region', { name: 'Model for each step' });
  fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));
  api.registerConnection.mockImplementation(async command => {
    const registered = { name: 'new', type: 'openai', base_url: command.base_url, auth_mode: 'none' };
    const next = { ...config, llm_routing: { ...config.llm_routing, providers: [...config.llm_routing.providers, registered] } };
    api.fetchConnections.mockResolvedValue({ revision: 'r2', config: next, providers: next.llm_routing.providers });
    return { operation_id: command.operation_id, state: 'registered_inactive', provider: registered, revision: 'r2' };
  });
  api.listConnectionModels.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await waitFor(() => expect(api.listConnectionModels).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));
  fireEvent.change(screen.getByLabelText('API address'), { target: { value: 'http://127.0.0.1:9123/changed' } });
  await act(async () => finish({ catalog: 'returned', models: ['obsolete'], revision: 'r2' }));
  expect(screen.queryByText('obsolete')).toBeNull(); expect(api.applyConnection).not.toHaveBeenCalled();
});
