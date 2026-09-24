// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import StepConnectLlm from './StepConnectLlm.jsx';

const api = vi.hoisted(() => ({ fetchAiPresets: vi.fn(), saveAiCredential: vi.fn(), listModels: vi.fn() }));
vi.mock('../../api/setupApi.js', () => ({ fetchAiPresets: api.fetchAiPresets, saveAiCredential: api.saveAiCredential }));
vi.mock('../../api/settingsApi.js', () => ({ listModels: api.listModels }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(mode, routing, onPatchRouting) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <StepConnectLlm mode={mode} routing={routing} onPatchRouting={onPatchRouting} />
  </QueryClientProvider>);
  return client;
}

const local = { name: 'local', type: 'openai', base_url: 'http://localhost:11434/v1', api_key_env: 'OLLAMA_API_KEY' };
const hosted = { name: 'hosted', type: 'openai', base_url: 'https://example.test/v1', api_key_env: 'HOSTED_KEY' };
const routing = { providers: [local], default: { provider: 'local', model: 'qwen3:8b' },
  feed: { provider: 'local', model: 'qwen3:8b' }, backlog: { provider: 'local', model: 'qwen3:8b' },
  deep_review: { provider: 'local', model: 'qwen3:8b' } };

it('Settings offers local and hosted recovery when no wizard mode is supplied', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [
    { id: 'local', label: 'Local', provider: local },
    { id: 'hosted', label: 'Hosted', provider: hosted },
  ] });
  show(undefined, { ...routing, providers: [hosted], default: { provider: 'hosted', model: 'remote' } }, vi.fn());
  expect(await screen.findByRole('button', { name: 'Local' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Hosted' })).toBeTruthy();
});

it('replacing a hosted key clears cached Doctor Ready in Settings', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'hosted', label: 'Hosted', provider: hosted }] });
  api.saveAiCredential.mockResolvedValue({ name: 'HOSTED_KEY', present: true });
  api.listModels.mockResolvedValue({ models: ['remote'] });
  const client = show('hosted', { providers: [hosted], default: { provider: 'hosted', model: 'remote' } }, vi.fn());
  client.setQueryData(['setup-doctor'], { ready: true });
  await screen.findByRole('button', { name: 'Hosted' });
  fireEvent.change(screen.getByPlaceholderText('Paste API key'), { target: { value: 'new-secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect & load models' }));
  await waitFor(() => expect(api.saveAiCredential).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false));
  client.clear();
});

it('switching to hosted replaces local overrides at every inference stage', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'hosted', label: 'Hosted', provider: hosted }] });
  const patch = vi.fn();
  show('hosted', routing, patch);
  fireEvent.click(await screen.findByRole('button', { name: 'Hosted' }));
  const next = patch.mock.calls.at(-1)[0];
  expect(next.default.provider).toBe('hosted');
  for (const stage of ['feed', 'backlog', 'deep_review']) {
    expect(next[stage]).toEqual({ provider: 'hosted', model: null });
  }
});

it('does not mistake a hosted model name for a selected local profile', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'local', label: 'Local', provider: local }],
    local_profiles: { hardware: { memory_gb: 32, disk_free_gb: 100 }, profiles: [
      { id: 'light', label: 'Light', compatible: true, provider: local,
        model: 'qwen3:8b', features: ['local'], tradeoff: 'Fast' },
    ] } });
  show('local', { ...routing, providers: [hosted], default: { provider: 'hosted', model: 'qwen3:8b' } }, vi.fn());
  expect((await screen.findByRole('button', { name: /Light/ })).getAttribute('aria-pressed')).toBe('false');
  expect(screen.getByRole('button', { name: 'Connect & load models' }).disabled).toBe(true);
  expect(screen.queryByLabelText('API key')).toBeNull();
});

it('full local existing endpoint permits a loopback non-Ollama URL without an API key field', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'local', label: 'Local', provider: local }],
    local_profiles: { hardware: { memory_gb: 32, disk_free_gb: 100 }, profiles: [
      { id: 'existing', label: 'Use existing endpoint', compatible: true, provider: local,
        model: null, features: ['local'], tradeoff: 'Use yours' },
    ] } });
  const patch = vi.fn();
  show('local', routing, patch);
  fireEvent.click(await screen.findByRole('button', { name: /Use existing endpoint/ }));
  fireEvent.change(screen.getByLabelText('Local endpoint URL'), { target: { value: 'http://127.0.0.1:1234/v1' } });
  expect(patch.mock.calls.at(-1)[0].providers.find((p) => p.name === 'local').base_url)
    .toBe('http://127.0.0.1:1234/v1');
  expect(screen.queryByLabelText('API key')).toBeNull();
});
