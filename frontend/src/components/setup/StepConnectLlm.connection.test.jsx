// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import StepConnectLlm from './StepConnectLlm.jsx';

const api = vi.hoisted(() => ({ fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(),
  previewConnection: vi.fn(), applyConnection: vi.fn(), readConnection: vi.fn(), testConnection: vi.fn() }));
vi.mock('../../api/connectionsApi.js', () => api);
vi.mock('../../api/setupApi.js', () => ({ fetchAiPresets: async () => ({ presets: [], local_profiles: { profiles: [] } }) }));
const provider = { name: 'old', type: 'openai', base_url: 'https://example.invalid/old', api_key_env: 'SYNTHETIC_POISON', auth_mode: 'none',
  temperature: 0.7, extra_body: { reasoning_effort: 'low' } };
const routing = { providers: [provider], default: { provider: 'old', model: 'old-model' },
  feed: { model: 'exception' }, backlog: { provider: 'old' } };
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it('Setup edits use canonical inactive registration and preserve explicit no-auth/options/routes', async () => {
  api.fetchConnections.mockResolvedValue({ revision: 'r1', config: { llm_routing: routing }, providers: [provider] });
  let finish;
  api.registerConnection.mockImplementation(command => new Promise(resolve => { finish = () => resolve({ operation_id: command.operation_id,
    provider: { ...provider, name: 'new', base_url: command.base_url }, revision: 'r2', state: 'registered_inactive' }); }));
  api.listConnectionModels.mockResolvedValue({ catalog: 'returned', models: ['new-model'], revision: 'r2' });
  const patch = vi.fn(), saved = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><StepConnectLlm routing={routing} mode="hosted" onPatchRouting={patch} onSaved={saved} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Change All steps', exact: true }));
  fireEvent.change(screen.getByLabelText('API address'), { target: { value: 'https://example.invalid/new' } });
  expect(screen.queryByLabelText('API key')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
  await waitFor(() => expect(api.registerConnection).toHaveBeenCalledTimes(1));
  const command = api.registerConnection.mock.calls[0][0];
  expect(command).toMatchObject({ source: 'old', auth: 'none', base_url: 'https://example.invalid/new', approved: true });
  expect(patch).not.toHaveBeenCalled();
  expect(routing.providers[0]).toEqual(provider);
  finish();
  await screen.findByLabelText('Model');
  expect(api.applyConnection).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Use as default' })).toBeNull();
  client.clear();
});
