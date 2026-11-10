// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(), validateSetup: vi.fn(), fetchAiPresets: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/setupApi.js', () => ({ validateSetup: api.validateSetup, fetchAiPresets: api.fetchAiPresets }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/setup/SetupGate.jsx', () => ({ dismissSetup: vi.fn() }));
vi.mock('../components/setup/StepConnectZotero.jsx', () => ({ default: () => <p>Zotero step</p> }));
vi.mock('../components/setup/StepConnectLlm.jsx', () => ({ default: () => <p>AI provider step</p> }));
vi.mock('../components/setup/StepDescribeResearch.jsx', () => ({ default: ({ draft, onPatchDraft, fieldErrors }) => (
  <><label>Research goal<input value={draft.research_goals_text} onChange={(event) => onPatchDraft({ research_goals_text: event.target.value })} /></label>
    {fieldErrors.map((error) => <p key={error.field}>{error.message}</p>)}</>
)}));
vi.mock('../components/setup/StepDone.jsx', () => ({ default: () => <h3>Setup saved</h3> }));

import SetupFlow from './SetupFlow.jsx';
import { configToFormState } from '../utils/configForm.js';

const config = {
  llm_enabled: true,
  research_goals: ['Replace with your first research focus area'],
  triage_criteria: [],
  output_language: 'English',
  llm_routing: {
    providers: [{ name: 'hosted', type: 'openai', base_url: 'https://example.test/v1', api_key_env: 'OPENAI_API_KEY' }],
    default: { provider: 'hosted', model: 'example-model' },
  },
  prestige: { weight: 0.15 },
};

beforeEach(() => {
  const storage = new Map();
  vi.stubGlobal('localStorage', { getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)), clear: () => storage.clear() });
  Object.values(api).forEach((mock) => mock.mockReset());
  api.fetchConfig.mockResolvedValue(config);
  api.fetchAiPresets.mockResolvedValue({ local_profiles: { profiles: [{ id: 'existing', compatible: true }] } });
  api.validateSetup.mockResolvedValue({ valid: true, field_errors: [] });
  api.updateConfig.mockImplementation(async (payload) => ({ config: payload }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['invalid existing configuration', 'missing configuration'])('makes %s terminal, repairable and skippable without inventing a model', async message => {
  api.fetchConfig.mockRejectedValue(new Error(message));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText(new RegExp(message))).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Open connection and configuration recovery' }).getAttribute('href')).toBe('/settings#ai-models');
  expect(screen.getByRole('button', { name: 'Skip for now' }).disabled).toBe(false);
  expect(screen.queryByText('Preparing setup…')).toBeNull();
  expect(api.updateConfig).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
  expect(screen.queryByText('Preparing setup…')).toBeNull();
  client.clear();
});

it('resumed setup retains its original revision rather than overwriting newer connection choices', async () => {
  const original = { ...config, _revision: 'r1', llm_enabled: false, research_goals: ['Local setup draft'] };
  const latest = { ...config, _revision: 'r2', research_goals: ['New saved research'] };
  window.localStorage.clear();
  window.localStorage.setItem('zs_setup_progress_v1', JSON.stringify({ mode: 'none', step: 3, revision: 'r1', draft: configToFormState(original) }));
  api.fetchConfig.mockResolvedValue(latest);
  api.updateConfig.mockRejectedValue(new Error('HTTP 409: stale configuration'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Finish' }));
  // The existing humanizeError owner removes the transport prefix for display.
  expect(await screen.findByText('stale configuration')).toBeTruthy();
  expect(api.updateConfig.mock.calls[0][0]._revision).toBe('r1');
  expect(screen.getByLabelText('Research goal').value).toBe('Local setup draft');
  fireEvent.click(screen.getByRole('button', { name: 'Discard setup draft and load saved settings' }));
  await waitFor(() => expect(screen.getByLabelText('Research goal').value).toBe('New saved research'));
  client.clear(); window.localStorage.clear();
});

it('cannot publish an old resumed provider DTO through Finish', async () => {
  const saved = { ...config, _revision: 'r1', research_goals: ['Saved research'] };
  const draft = configToFormState(saved);
  draft.llm_routing.providers[0].base_url = 'https://example.invalid/changed';
  window.localStorage.setItem('zs_setup_progress_v1', JSON.stringify({ mode: 'hosted', step: 3, revision: 'r1', draft }));
  api.fetchConfig.mockResolvedValue(saved);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Finish' }));
  expect(await screen.findByText('Connection changes must be saved through Connect, impact and Use as default before finishing Setup.')).toBeTruthy();
  expect(api.updateConfig).not.toHaveBeenCalled();
  expect(saved.llm_routing.providers[0].base_url).toBe(config.llm_routing.providers[0].base_url);
  client.clear();
});

it('completes ML-only setup after validation and saves the personalized goal', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(['setup-doctor'], { ready: true }); // from an older configuration
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);

  await screen.findByRole('radio', { name: /Triage without an LLM/ });
  fireEvent.click(screen.getByRole('radio', { name: /Triage without an LLM/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('Zotero step');
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.change(screen.getByLabelText('Research goal'), { target: { value: 'Evidence synthesis for clinical agents' } });
  fireEvent.click(screen.getByRole('button', { name: 'Finish' }));

  await screen.findByRole('heading', { name: 'Setup saved' });
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  const [validated] = api.validateSetup.mock.calls[0];
  const [saved] = api.updateConfig.mock.calls[0];
  expect(validated.test_connection).toBe(false);
  expect(validated.config.llm_enabled).toBe(false);
  expect(saved.llm_enabled).toBe(false);
  expect(saved.research_goals).toEqual(['Evidence synthesis for clinical agents']);
  expect(saved.prestige).toEqual(config.prestige);
  // A shared invalidation tombstone fences late checks; removed cache entries cannot do that.
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
  client.clear();
});

it('presents the setup-mode choice before Zotero or provider setup', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);

  await waitFor(() => expect(screen.queryByText('Preparing setup…')).toBeNull());
  expect(screen.getByRole('radio', { name: /Full local/i })).toBeTruthy();
  expect(screen.getByRole('radio', { name: /Hosted model/i })).toBeTruthy();
  expect(screen.getByRole('radio', { name: /Triage without an LLM/i })).toBeTruthy();
  expect(screen.queryByText('Zotero step')).toBeNull();
  expect(screen.queryByText('AI provider step')).toBeNull();
  client.clear();
});

it.each([
  ['Full local', { ...config.llm_routing, providers: [
    { ...config.llm_routing.providers[0], name: 'local', base_url: 'https://remote.test/v1' }],
    default: { provider: 'local', model: 'remote' } }],
  ['Hosted model', { ...config.llm_routing,
    providers: [...config.llm_routing.providers,
      { ...config.llm_routing.providers[0], name: 'local', base_url: 'http://localhost:11434/v1' }],
    feed: { provider: 'local', model: 'qwen3:8b' } }],
])('cannot finish %s with a provider/stage outside its inference boundary', async (choice, llm_routing) => {
  api.fetchConfig.mockResolvedValue({ ...config, llm_routing });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('radio', { name: new RegExp(choice) }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('Zotero step');
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('AI provider step');
  expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(true);
  client.clear();
});

it.each(['127.0.0.2', '127.1.2.3', '[::ffff:127.0.0.2]', '0.0.0.0', '[::]',
  '127.1', '2130706433', '0177.0.0.1', '0x7f000001', 'localhost.', 'model.localhost'])(
  'accepts backend-supported literal loopback %s in Full local mode', async (host) => {
    api.fetchConfig.mockResolvedValue({ ...config, llm_routing: {
      providers: [{ name: 'local', type: 'openai', base_url: `http://${host}:8080/v1`, api_key_env: 'LOCAL_KEY' }],
      default: { provider: 'local', model: 'fixture' },
    } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);
    fireEvent.click(await screen.findByRole('radio', { name: /Full local/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Zotero step');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('AI provider step');
    expect(screen.getByRole('button', { name: 'Next' }).disabled).toBe(false);
    client.clear();
  },
);

it('returns to the research step on validation errors and does not save invalid setup', async () => {
  api.validateSetup.mockResolvedValue({ valid: false, field_errors: [
    { field: 'research_goals', message: 'Add a research goal.' },
  ] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);

  await screen.findByRole('radio', { name: /Triage without an LLM/ });
  fireEvent.click(screen.getByRole('radio', { name: /Triage without an LLM/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('Zotero step');
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.change(screen.getByLabelText('Research goal'), { target: { value: 'Valid-looking goal' } });
  fireEvent.click(screen.getByRole('button', { name: 'Finish' }));

  expect(await screen.findByText('Add a research goal.')).toBeTruthy();
  expect(api.validateSetup).toHaveBeenCalledTimes(1);
  expect(api.updateConfig).not.toHaveBeenCalled();
  client.clear();
});
