// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/AiModelsSection.jsx', () => ({ default: ({ enabled, onEnabledChange }) => (
  <button type="button" onClick={() => onEnabledChange(!enabled)}>{enabled ? 'Disable AI' : 'Enable AI'}</button>
)}));
vi.mock('../components/settings/EssentialsSection.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/UniversityAccessPanel.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/CalibrationCard.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/DeploymentCard.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/RssFeedsSection.jsx', () => ({ default: () => null }));

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

it('saves an ML-only preference while round-tripping unsurfaced configuration', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
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
