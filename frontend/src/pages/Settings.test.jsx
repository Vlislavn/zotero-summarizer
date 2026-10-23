// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/AiModelsSection.jsx', () => ({ default: ({ enabled, onEnabledChange, open }) => (
  <section id="ai-models">
    <p>{open ? 'AI disclosure open' : 'AI disclosure closed'}</p>
    <button type="button" onClick={() => onEnabledChange(!enabled)}>{enabled ? 'Disable AI' : 'Enable AI'}</button>
  </section>
)}));
vi.mock('../components/settings/EssentialsSection.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/UniversityAccessPanel.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/CalibrationCard.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/DeploymentCard.jsx', () => ({ default: ({ onOpenAiSettings, onOpenSettings }) => (
  <><button type="button" onClick={onOpenAiSettings}>Review endpoint settings</button>
    <button type="button" onClick={() => onOpenSettings('rss-feeds')}>Add a source in Settings</button></>
)}));
vi.mock('../components/settings/RssFeedsSection.jsx', () => ({ default: () => <input aria-label="RSS recovery URL" /> }));

import Settings from './Settings.jsx';

const config = {
  llm_enabled: true,
  research_goals: ['Clinical agents'],
  triage_criteria: ['Evidence quality'],
  output_language: 'English',
  llm_routing: { providers: [], default: {}, feed: {}, backlog: {}, deep_review: {} },
  prestige: { weight: 0.15, custom_venues: ['Example Journal'] },
};

beforeEach(() => {
  api.fetchConfig.mockReset().mockResolvedValue(config);
  api.updateConfig.mockReset().mockImplementation(async (payload) => ({ config: payload }));
});
afterEach(cleanup);

it('typed recovery opens existing AI disclosure without saving or changing mode', async () => {
  window.history.replaceState(null, '', '/settings');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  expect(await screen.findByText('AI disclosure closed')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Review endpoint settings' }));
  expect(screen.getByText('AI disclosure open')).toBeTruthy();
  expect(api.updateConfig).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Disable AI' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Save changes' }).disabled).toBe(true);
  client.clear();
});

it('source recovery focuses the existing RSS controls without saving or refreshing', async () => {
  window.history.replaceState(null, '', '/settings');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Add a source in Settings' }));
  expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'RSS recovery URL' }));
  expect(api.updateConfig).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Save changes' }).disabled).toBe(true);
  client.clear();
});

it('wizard source hash focuses the same RSS controls after async config loads', async () => {
  window.history.replaceState(null, '', '/settings#rss-feeds');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  const input = await screen.findByRole('textbox', { name: 'RSS recovery URL' });
  await waitFor(() => expect(document.activeElement).toBe(input));
  expect(api.updateConfig).not.toHaveBeenCalled();
  client.clear();
});

it('wizard AI hash opens the same existing disclosure without automatic effects', async () => {
  window.history.replaceState(null, '', '/settings#ai-models');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  expect(await screen.findByText('AI disclosure open')).toBeTruthy();
  expect(api.updateConfig).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Save changes' }).disabled).toBe(true);
  client.clear();
});

it('saves an ML-only preference while round-tripping unsurfaced configuration', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(['setup-doctor'], { ready: true });
  window.history.replaceState(null, '', '/settings#university-access');
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);

  await screen.findByRole('heading', { name: 'Settings' });
  expect(document.getElementById('university-access').open).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Disable AI' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  const [payload] = api.updateConfig.mock.calls[0];
  expect(payload.llm_enabled).toBe(false);
  expect(payload.prestige).toEqual(config.prestige);
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
  expect(await screen.findByText('Saved successfully')).toBeTruthy();
  client.clear();
});

it('keeps edits visible and reports a failed save without a success message', async () => {
  api.updateConfig.mockRejectedValue(new Error('disk full'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);

  await screen.findByRole('heading', { name: 'Settings' });
  const aiButton = await screen.findByRole('button', { name: /^(Enable|Disable) AI$/ });
  const nextAiButton = aiButton.textContent === 'Enable AI' ? 'Disable AI' : 'Enable AI';
  fireEvent.click(aiButton);
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

  expect(await screen.findByText(/Save failed:/)).toBeTruthy();
  expect(screen.queryByText('Saved successfully')).toBeNull();
  expect(screen.getByRole('button', { name: nextAiButton })).toBeTruthy();
  client.clear();
});
