// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import StepConnectLlm from './StepConnectLlm.jsx';

const api = vi.hoisted(() => ({ fetchAiPresets: vi.fn(), saveAiCredential: vi.fn(), listModels: vi.fn() }));
vi.mock('../../api/setupApi.js', () => ({ fetchAiPresets: api.fetchAiPresets, saveAiCredential: api.saveAiCredential }));
vi.mock('../../api/settingsApi.js', () => ({ listModels: api.listModels }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function show(mode, routing, onPatchRouting, extra = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <StepConnectLlm mode={mode} routing={routing} onPatchRouting={onPatchRouting} {...extra} />
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
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }));
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
  expect(screen.getByRole('button', { name: 'List available models' }).disabled).toBe(true);
  expect(screen.queryByLabelText('API key')).toBeNull();
});

it('withholds local progression for an incompatible saved profile and names the resource floors', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'local', label: 'Local', provider: local }],
    local_profiles: { hardware: { memory_gb: 8, disk_free_gb: 100 }, profiles: [
      { id: 'light', label: 'Light', compatible: false, provider: local, model: 'qwen3:8b',
        size_gb: 5.2, min_memory_gb: 12, min_disk_gb: 8, compatibility_detail: 'needs 12 GB memory',
        features: ['summaries'], tradeoff: 'Fast' },
    ] } });
  const valid = vi.fn();
  show('local', routing, vi.fn(), { onValidityChange: valid });
  expect((await screen.findByRole('button', { name: /Light/ })).disabled).toBe(true);
  await waitFor(() => expect(valid).toHaveBeenLastCalledWith(false));
  expect(screen.getByText(/12 GB memory.*8 GB free disk/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'List available models' }).disabled).toBe(true);
  expect(api.listModels).not.toHaveBeenCalled();
  expect(api.saveAiCredential).not.toHaveBeenCalled();
});

it('keeps a manually configured existing local model editable before the runtime is reachable', async () => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'local', label: 'Local', provider: local }],
    local_profiles: { hardware: { memory_gb: 32, disk_free_gb: 100 }, profiles: [
      { id: 'existing', label: 'Use existing endpoint', compatible: true, provider: local,
        model: null, features: ['local'], tradeoff: 'Use yours' },
    ] } });
  const patch = vi.fn();
  show('local', { ...routing, default: { provider: 'local', model: 'my-local-model' } }, patch);
  fireEvent.change(await screen.findByLabelText(/^Local model name/), { target: { value: 'served-model' } });
  expect(patch.mock.calls.at(-1)[0].default.model).toBe('served-model');
  expect(screen.queryByLabelText('API key')).toBeNull();
  expect(api.listModels).not.toHaveBeenCalled();
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

it.each([422, 403, 503, undefined])('retires health on credential-save failure %s, including validation after server invalidation', async (status) => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'hosted', label: 'Hosted', provider: hosted }] });
  api.saveAiCredential.mockRejectedValueOnce(Object.assign(new Error('Credential save failed'), { status }));
  const onTested = vi.fn();
  const client = show('hosted', { providers: [hosted], default: { provider: 'hosted', model: 'remote' } }, vi.fn(), { onTested });
  client.setQueryData(['setup-doctor'], { ready: true });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  await screen.findByRole('button', { name: 'Hosted' });
  fireEvent.change(screen.getByPlaceholderText('Paste API key'), { target: { value: 'new-secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }));
  await screen.findByText('Credential save failed');
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
  expect(invalidate).toHaveBeenCalledTimes(1);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['setup-doctor'] });
  expect(api.saveAiCredential).toHaveBeenCalledTimes(1);
  expect(api.saveAiCredential).toHaveBeenCalledWith('HOSTED_KEY', 'new-secret');
  expect(api.listModels).not.toHaveBeenCalled();
  expect(onTested).toHaveBeenLastCalledWith(false);
  client.clear();
});

it.each([false, true])('a model-list error does not add a write invalidation; credential replacement %s', async (replace) => {
  api.fetchAiPresets.mockResolvedValue({ presets: [{ id: 'hosted', label: 'Hosted', provider: hosted,
    credential: { present: true } }] });
  api.saveAiCredential.mockResolvedValue({ name: 'HOSTED_KEY', present: true });
  api.listModels.mockRejectedValueOnce(Object.assign(new Error('Model list unavailable'), { status: 503 }));
  const client = show('hosted', { providers: [hosted], default: { provider: 'hosted', model: 'remote' } }, vi.fn());
  client.setQueryData(['setup-doctor'], { ready: true });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  await screen.findByRole('button', { name: 'Hosted' });
  if (replace) fireEvent.change(screen.getByPlaceholderText('Stored securely — paste to replace'), { target: { value: 'replacement' } });
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }));
  await screen.findByText('Model list unavailable');
  expect(api.listModels).toHaveBeenCalledTimes(1);
  expect(api.saveAiCredential).toHaveBeenCalledTimes(replace ? 1 : 0);
  expect(invalidate).toHaveBeenCalledTimes(replace ? 1 : 0);
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(!replace);
  client.clear();
});
