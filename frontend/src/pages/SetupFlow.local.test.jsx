// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(), listModels: vi.fn(),
  fetchAiPresets: vi.fn(), saveAiCredential: vi.fn(), validateSetup: vi.fn(), detectZotero: vi.fn(),
  updatePaths: vi.fn(), fetchDoctorStatus: vi.fn(), runDoctor: vi.fn(), cancelDoctor: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/setupApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
import SetupFlow from './SetupFlow.jsx';

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
const initialConfig = { llm_enabled: true, research_goals: ['Replace with your research focus'],
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

it('rechecks hardware compatibility when the model picker replaces the selected profile model', async () => {
  api.listModels.mockResolvedValue({ models: ['qwen3:8b', 'qwen3:30b'] });
  const view = show();
  try {
    fireEvent.click(await screen.findByRole('radio', { name: /Full local/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByRole('heading', { name: 'Zotero sync' });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(await screen.findByRole('button', { name: /^Light/ }));
    fireEvent.click(screen.getByRole('button', { name: 'List available models' }));
    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'qwen3:30b' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(true));
    expect(screen.getByRole('button', { name: /^Balanced/ }).getAttribute('aria-pressed')).toBe('true');
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
    for (const stage of ['default', 'feed', 'backlog', 'deep_review']) {
      expect(saved.llm_routing[stage]).toMatchObject({ provider: 'local', model: 'qwen3:8b' });
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
    expect(screen.getByText(/Local load safety is not established/)).toBeTruthy();
    expect(api.runDoctor).toHaveBeenCalledWith(null);
    expect(screen.getByRole('button', { name: 'Open Today' }).disabled).toBe(true);
    expect(screen.getByRole('link', { name: 'Review endpoint settings' }).getAttribute('href')).toBe('/settings#ai-models');
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
