// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(), listModels: vi.fn(),
  fetchAiPresets: vi.fn(), saveAiCredential: vi.fn(), validateSetup: vi.fn(), detectZotero: vi.fn(),
  updatePaths: vi.fn(), fetchDoctorStatus: vi.fn(), runDoctor: vi.fn(), cancelDoctor: vi.fn(),
  fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(), previewConnection: vi.fn(),
  applyConnection: vi.fn(), readConnection: vi.fn(), testConnection: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/setupApi.js', () => api);
vi.mock('../api/connectionsApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
import SetupFlow from './SetupFlow.jsx';
import { configToFormState } from '../utils/configForm.js';

const provider = { name: 'local', type: 'openai', base_url: 'http://localhost:11434/v1', api_key_env: 'OLLAMA_API_KEY' };
const catalog = { presets: [{ id: 'local', label: 'Local', provider }], local_profiles: {
  hardware: { memory_gb: 16, disk_free_gb: 100 }, profiles: [
    { id: 'light', label: 'Light', model: 'qwen3:8b', runtime: 'Ollama', size_gb: 5.2,
      min_memory_gb: 12, min_disk_gb: 8, compatible: true, compatibility_detail: 'Meets static floors',
      features: ['triage', 'summaries', 'Q&A'], tradeoff: 'Lower memory, lean reviews', provider,
      source: 'https://ollama.com/library/qwen3:8b', pull_command: 'ollama pull qwen3:8b' },
    { id: 'balanced', label: 'Balanced', model: 'qwen3:30b', runtime: 'Ollama', size_gb: 19,
      min_memory_gb: 32, min_disk_gb: 22, features: ['stronger review'], tradeoff: 'Higher memory',
      compatible: false, compatibility_detail: 'needs 32 GB memory', provider },
  ],
} };
const initialConfig = { _revision: 'r1', llm_enabled: true, research_goals: ['Replace with your research focus'],
  triage_criteria: [], output_language: 'English', llm_routing: { providers: [], default: {} } };
const unknown = { ready: false, status: 'needs_action', checks: [
  { id: 'runtime_model', status: 'ready', message: 'Runtime serves every configured model' },
  { id: 'llm_inference', status: 'needs_action', message: 'LLM verification needs action',
    detail: 'local/qwen3:8b: policy_missing; not submitted',
    load_receipts: [{ reason_code: 'policy_missing', attempted: false, admission: { status: 'unknown' } }],
    recovery: { action: 'open_ai_settings', label: 'Review endpoint settings' } },
], modes: { local_inference: 'needs_action', offline_ready: 'needs_action', strict_offline: 'not_started' } };

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/setup']}>
    <SetupFlow />
  </MemoryRouter></QueryClientProvider>);
  return { ...view, client };
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  const storage = new Map();
  vi.stubGlobal('localStorage', { getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value) });
  api.fetchConfig.mockResolvedValue(initialConfig);
  let connection = { revision: 'r1', providers: [], config: initialConfig };
  api.fetchConnections.mockImplementation(async () => connection);
  api.registerConnection.mockImplementation(async command => {
    const candidate = { ...provider, name: 'candidate', auth_mode: 'none', base_url: command.base_url };
    connection = { revision: 'r2', providers: [candidate], config: { ...initialConfig, _revision: 'r2', llm_routing: { ...initialConfig.llm_routing, providers: [candidate] } } };
    return { operation_id: command.operation_id, provider: candidate, revision: 'r2', state: 'registered_inactive' };
  });
  api.listConnectionModels.mockResolvedValue({ revision: 'r2', catalog: 'returned', models: ['qwen3:8b', 'qwen3:30b'] });
  api.previewConnection.mockImplementation(async command => ({ revision: 'r2', preview: 'preview', ready_to_apply: true,
    impact: { stages: ['feed', 'backlog', 'deep_review'].map(stage => ({ stage, before: {}, after: { provider: 'candidate', model: command.model }, after_explicit: {} })), telegram: { selection: null }, shared_authorization: {} } }));
  api.applyConnection.mockImplementation(async command => {
    connection = { ...connection, revision: 'r3', config: { ...connection.config, _revision: 'r3', llm_routing: { ...connection.config.llm_routing, default: { provider: 'candidate', model: command.model } } } };
    return { provider: connection.providers[0], revision: 'r3', state: 'selection_saved', selection_matches: true, config: connection.config };
  });
  api.fetchAiPresets.mockResolvedValue(catalog);
  api.detectZotero.mockResolvedValue({ candidates: [] });
  api.validateSetup.mockResolvedValue({ valid: true, field_errors: [] });
  api.updateConfig.mockImplementation(async (config) => {
    api.fetchConfig.mockResolvedValue(config);
    return { config };
  });
  api.fetchDoctorStatus.mockResolvedValue({ ready: false, status: 'not_started', checks: [], modes: {} });
  api.runDoctor.mockImplementation(async () => {
    api.fetchDoctorStatus.mockResolvedValue(unknown);
    return unknown;
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['registration', 'selection'])('a fresh foreign %s publication cannot advance a dirty Setup draft or overwrite its writer', async phase => {
  const original = { ...initialConfig, research_goals: ['Original research'],
    llm_routing: { providers: [provider], default: { provider: 'local', model: 'qwen3:8b' } } };
  let server = original;
  let scene;
  const candidate = { ...provider, name: 'candidate', auth_mode: 'none', base_url: 'http://localhost:11435/v1' };
  function foreignWrite() {
    server = { ...server, _revision: 'r9', research_goals: ['Concurrent research'] };
    scene.client.setQueryData(['runtime-config'], server);
    scene.client.setQueryData(['llm-connections'], { revision: 'r9', config: server, providers: server.llm_routing.providers });
  }
  api.fetchConfig.mockImplementation(async () => server);
  api.fetchConnections.mockImplementation(async () => ({ revision: server._revision, config: server, providers: server.llm_routing.providers }));
  api.registerConnection.mockImplementation(async command => {
    server = { ...server, _revision: 'r2', llm_routing: { ...server.llm_routing, providers: [provider, candidate] } };
    if (phase === 'registration') foreignWrite();
    return { operation_id: command.operation_id, provider: candidate, revision: 'r2', state: 'registered_inactive' };
  });
  api.applyConnection.mockImplementation(async () => {
    server = { ...server, _revision: 'r3', llm_routing: { ...server.llm_routing, default: { provider: 'candidate', model: 'qwen3:8b' } } };
    const own = server;
    foreignWrite();
    return { provider: candidate, revision: 'r3', state: 'selection_saved', selection_matches: true, config: own };
  });
  api.updateConfig.mockImplementation(async payload => {
    if (payload._revision !== server._revision) throw Object.assign(new Error('Revision changed'), { status: 409 });
    server = { ...payload, _revision: 'r10' };
    return { config: server };
  });
  localStorage.setItem('zs_setup_progress_v1', JSON.stringify({ mode: 'local', step: 2, maxStepReached: 3,
    revision: 'r1', baseConfig: original, draft: { ...configToFormState(original), research_goals_text: 'My retained draft' } }));
  scene = show();
  try {
    fireEvent.click(await screen.findByRole('button', { name: 'Add provider' }));
    fireEvent.change(screen.getByLabelText('API address'), { target: { value: candidate.base_url } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
    if (phase === 'selection') {
      await waitFor(() => expect(screen.getByLabelText('Model').disabled).toBe(false));
      fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'qwen3:8b' } });
      fireEvent.click(await screen.findByRole('button', { name: 'Use as default' }));
    }
    await waitFor(() => expect(screen.getByRole('button', { name: 'Test saved model' }).disabled).toBe(false));
    expect(scene.client.getQueryData(['llm-connections']).revision).toBe('r9');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByLabelText(/^Research goals/).value).toBe('My retained draft');
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
    expect(server.research_goals).toEqual(['Concurrent research']);
    expect(api.updateConfig.mock.calls[0][0]._revision).toBe(phase === 'registration' ? 'r1' : 'r2');
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(api.testConnection).not.toHaveBeenCalled();
    expect(api.registerConnection).toHaveBeenCalledTimes(1);
    expect(api.applyConnection).toHaveBeenCalledTimes(phase === 'registration' ? 0 : 1);
    if (phase === 'registration') expect(api.listConnectionModels).not.toHaveBeenCalled();
  } finally { scene.unmount(); scene.client.clear(); }
});

it('rechecks hardware compatibility when the model picker replaces the selected profile model', async () => {
  api.listModels.mockResolvedValue({ models: ['qwen3:8b', 'qwen3:30b'] });
  const view = show();
  try {
    fireEvent.click(await screen.findByRole('radio', { name: /Full local/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('heading', { name: 'Zotero sync' });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(await screen.findByRole('button', { name: /^Light/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
    fireEvent.change(await screen.findByLabelText('Model'), { target: { value: 'qwen3:30b' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Use as default' }));
    await screen.findByText(/Selection saved/);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(true));
    expect(screen.getByRole('button', { name: /^Balanced/ }).disabled).toBe(true);
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(api.updateConfig).not.toHaveBeenCalled();
  } finally {
    view.unmount();
    view.client.clear();
  }
});

it('wires Full local through safe profile, save, real-check request, honest UNKNOWN and Settings recovery', async () => {
  let view = show();
  try {
    fireEvent.click(await screen.findByRole('radio', { name: /Full local/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('heading', { name: 'Zotero sync' });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    const light = await screen.findByRole('button', { name: /^Light/ });
    expect(screen.getByRole('button', { name: /^Balanced/ }).disabled).toBe(true);
    expect(screen.queryByLabelText('API key')).toBeNull();
    expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(true);
    fireEvent.click(light);
    expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Connect provider' }));
    await screen.findByLabelText('Model');
    fireEvent.click(await screen.findByRole('button', { name: 'Use as default' }));
    await screen.findByText(/Selection saved/);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(false));
    expect(screen.getByRole('link', { name: 'Official model source' }).href).toBe('https://ollama.com/library/qwen3:8b');
    expect(screen.getByText(/approximately 5.2 GB/)).toBeTruthy();
    expect(api.listModels).not.toHaveBeenCalled();
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(api.saveAiCredential).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Set up Zotero Summarizer' }));
    fireEvent.change(screen.getByLabelText(/^Research goals/), { target: { value: 'Reproducible clinical agents' } });
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    await screen.findByRole('heading', { name: 'Setup saved' });
    const saved = api.updateConfig.mock.calls[0][0];
    expect(saved.llm_routing.default).toEqual({ provider: 'candidate', model: 'qwen3:8b' });
    for (const stage of ['feed', 'backlog', 'deep_review']) {
      expect(saved.llm_routing[stage]).toBeUndefined(); // Inherit, never homogenize explicit exceptions.
    }
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true);

    view.unmount();
    view.client.clear();
    view = show();
    await screen.findByRole('heading', { name: 'Setup saved' });
    expect(api.updateConfig).toHaveBeenCalledTimes(1);
    fireEvent.click(await screen.findByRole('button', { name: 'Verify setup' }));
    expect(await screen.findByText('LLM verification needs action')).toBeTruthy();
    expect(screen.getByText(/Not enough free-memory information to run a local model safely/)).toBeTruthy();
    expect(api.runDoctor).toHaveBeenCalledWith(null);
    expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true);
    expect(screen.getByRole('link', { name: 'Review endpoint settings' }).getAttribute('href')).toBe('/settings?task=connection');
    expect(api.runDoctor).toHaveBeenCalledTimes(1);
  } finally {
    view.unmount();
    view.client.clear();
  }
});

it.each([['qwen3:8b', false], ['qwen3:30b', true]])('revalidates a resumed research step for model %s without mounting the model editor', async (model, blocked) => {
  localStorage.setItem('zs_setup_progress_v1', JSON.stringify({ mode: 'local', step: 3,
    draft: { llm_enabled: true, research_goals_text: 'Synthetic goal', triage_criteria_text: '', output_language: 'English',
      llm_routing: { providers: [provider], default: { provider: 'local', model } } } }));
  const view = show();
  try {
    const finish = await screen.findByRole('button', { name: 'Finish' });
    await waitFor(() => expect(api.fetchAiPresets).toHaveBeenCalled());
    await waitFor(() => expect(finish.disabled).toBe(blocked));
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(api.listModels).not.toHaveBeenCalled();
    expect(api.updateConfig).not.toHaveBeenCalled();
  } finally { view.unmount(); view.client.clear(); }
});

it.each([undefined, 503, 404, 422])('wizard path save status %s retires only uncertain health without calling checks', async (status) => {
  api.updatePaths.mockRejectedValueOnce(Object.assign(new Error('Wizard path save failed'), { status }));
  const view = show();
  view.client.setQueryData(['setup-doctor'], { ready: true });
  try {
    fireEvent.click(await screen.findByRole('radio', { name: /Triage without an LLM/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.change(await screen.findByLabelText(/^Zotero data directory/), { target: { value: '/synthetic/zotero' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save path' }));
    await screen.findByText('Wizard path save failed');
    expect(view.client.getQueryData(['setup-doctor'])?.ready).toBe(status === 422);
    expect(api.updatePaths).toHaveBeenCalledTimes(1);
    expect(api.updatePaths.mock.calls[0][0]).toEqual({ zotero_data_dir: '/synthetic/zotero' });
    expect(screen.queryByText('Restart required to apply new paths.')).toBeNull();
    expect(api.updateConfig).not.toHaveBeenCalled();
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(api.listModels).not.toHaveBeenCalled();
    expect(api.saveAiCredential).not.toHaveBeenCalled();
  } finally { view.unmount(); view.client.clear(); }
});

it.each([undefined, 503, 404, 422])('wizard Finish status %s preserves its original error without claiming completion', async (status) => {
  api.updateConfig.mockRejectedValueOnce(Object.assign(new Error('Wizard config save failed'), { status }));
  const view = show();
  view.client.setQueryData(['setup-doctor'], { ready: true });
  try {
    fireEvent.click(await screen.findByRole('radio', { name: /Triage without an LLM/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('heading', { name: 'Zotero sync' });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.change(screen.getByLabelText(/^Research goals/), { target: { value: 'Synthetic research goal' } });
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    await screen.findByText('Wizard config save failed');
    expect(view.client.getQueryData(['setup-doctor'])?.ready).toBe(status === 422);
    expect(screen.queryByRole('heading', { name: 'Setup saved' })).toBeNull();
    expect(screen.getByLabelText(/^Research goals/).value).toBe('Synthetic research goal');
    expect(api.validateSetup).toHaveBeenCalledTimes(1);
    expect(api.updateConfig).toHaveBeenCalledTimes(1);
    expect(api.updateConfig.mock.calls[0][0].research_goals).toEqual(['Synthetic research goal']);
    expect(api.runDoctor).not.toHaveBeenCalled();
    expect(api.listModels).not.toHaveBeenCalled();
  } finally { view.unmount(); view.client.clear(); }
});
