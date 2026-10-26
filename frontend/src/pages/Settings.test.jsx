// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(),
  fetchDoctorStatus: vi.fn(), runDoctor: vi.fn(), cancelDoctor: vi.fn(),
  fetchUniversityAccessStatus: vi.fn(), runUniversityLogin: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/libraryApi.js', () => api);
vi.mock('../api/setupApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/AiModelsSection.jsx', () => ({ default: ({ enabled, onEnabledChange, open }) => (
  <section id="ai-models">
    <p>{open ? 'AI disclosure open' : 'AI disclosure closed'}</p>
    <button type="button" onClick={() => onEnabledChange(!enabled)}>{enabled ? 'Disable AI' : 'Enable AI'}</button>
  </section>
)}));
vi.mock('../components/settings/EssentialsSection.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/CalibrationCard.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/RssFeedsSection.jsx', () => ({ default: () => (
  <section id="rss-feeds"><input aria-label="RSS recovery URL" /></section>
)}));

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
  api.runDoctor.mockReset();
  api.cancelDoctor.mockReset();
  api.fetchUniversityAccessStatus.mockReset().mockResolvedValue({ browser_available: true, login_url: '' });
  api.runUniversityLogin.mockReset().mockReturnValue(new Promise(() => {}));
  api.fetchDoctorStatus.mockReset().mockResolvedValue({ ready: false, status: 'needs_action', checks: [
    { id: 'llm_inference', status: 'needs_action', message: 'Review the configured endpoint',
      recovery: { action: 'open_ai_settings', label: 'Review endpoint settings' } },
    { id: 'rss_source', status: 'needs_action', message: 'No enabled source',
      recovery: { action: 'open_sources_settings', label: 'Add a source in Settings' } },
  ] });
});
afterEach(cleanup);

it('typed recovery opens existing AI disclosure without saving or changing mode', async () => {
  window.history.replaceState(null, '', '/settings');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  expect(await screen.findByText('AI disclosure closed')).toBeTruthy();
  const recovery = await screen.findByRole('link', { name: 'Review endpoint settings' });
  expect(recovery.getAttribute('href')).toBe('/settings#ai-models');
  fireEvent.click(recovery);
  await screen.findByText('AI disclosure open');
  expect(api.runDoctor).not.toHaveBeenCalled();
  expect(api.updateConfig).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Disable AI' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Save changes' }).disabled).toBe(true);
  client.clear();
});

it('source recovery focuses the existing RSS controls without saving or refreshing', async () => {
  window.history.replaceState(null, '', '/settings');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  const recovery = await screen.findByRole('link', { name: 'Add a source in Settings' });
  expect(recovery.getAttribute('href')).toBe('/settings#rss-feeds');
  fireEvent.click(recovery);
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'RSS recovery URL' })));
  expect(api.runDoctor).not.toHaveBeenCalled();
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

it('admits library login only after the edited URL is authoritatively saved, not from stale readiness', async () => {
  let finishSave;
  api.updateConfig.mockImplementation((payload) => new Promise(resolve => { finishSave = () => resolve({ config: payload }); }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  window.history.replaceState(null, '', '/settings#university-access');
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  const url = await screen.findByRole('textbox', { name: 'Library login URL' });
  const login = screen.getByRole('button', { name: 'Log in to library' });
  expect(login.disabled).toBe(true);
  fireEvent.change(url, { target: { value: 'https://synthetic-university.invalid/login' } });
  expect(login.disabled).toBe(true);
  fireEvent.click(login);
  expect(api.runUniversityLogin).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  expect(login.disabled).toBe(true);
  expect(api.runUniversityLogin).not.toHaveBeenCalled();
  await act(async () => { finishSave(); });
  await waitFor(() => expect(login.disabled).toBe(false));
  expect(client.getQueryData(['runtime-config']).university_access.login_url).toBe('https://synthetic-university.invalid/login');
  expect(client.getQueryData(['university-access-status']).login_url).toBe('');
  fireEvent.click(login);
  await waitFor(() => expect(api.runUniversityLogin).toHaveBeenCalledTimes(1));
  expect(api.updateConfig).toHaveBeenCalledTimes(1);
  client.clear();
});

it.each(['ua_enabled', 'ua_ezproxy_prefix', 'ua_cookie_browser', 'ua_login_url'])(
  'blocks a failed or unconfirmed save of %s while keeping the edited destination visible', async (field) => {
    api.fetchConfig.mockResolvedValue({ ...config, university_access: { enabled: true, login_url: 'https://saved.invalid/login' } });
    api.updateConfig.mockRejectedValue(new TypeError('Save response lost'));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    window.history.replaceState(null, '', '/settings#university-access');
    render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
    const url = await screen.findByRole('textbox', { name: 'Library login URL' });
    const login = screen.getByRole('button', { name: 'Log in to library' });
    expect(login.disabled).toBe(false);
    if (field === 'ua_enabled') fireEvent.click(screen.getByRole('checkbox', { name: 'Enable browser PDF fetch for the review fleet' }));
    if (field === 'ua_ezproxy_prefix') fireEvent.change(screen.getByRole('textbox', { name: /EZproxy prefix/ }), { target: { value: 'https://proxy.invalid/?url=' } });
    if (field === 'ua_cookie_browser') fireEvent.change(screen.getByRole('combobox', { name: /Reuse an existing browser login/ }), { target: { value: 'chrome' } });
    if (field === 'ua_login_url') fireEvent.change(url, { target: { value: 'https://edited.invalid/login' } });
    expect(login.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/Save failed:/);
    expect(login.disabled).toBe(true);
    expect(url.value).toBe(field === 'ua_login_url' ? 'https://edited.invalid/login' : 'https://saved.invalid/login');
    fireEvent.click(login);
    expect(api.runUniversityLogin).not.toHaveBeenCalled();
    expect(client.getQueryData(['runtime-config']).university_access.login_url).toBe('https://saved.invalid/login');
    client.clear();
  },
);

it('does not block unchanged saved library access for unrelated edits, but does block any active Save', async () => {
  window.history.replaceState(null, '', '/settings#university-access');
  api.fetchConfig.mockResolvedValue({ ...config, university_access: { login_url: 'https://saved.invalid/login' } });
  let finishSave;
  api.updateConfig.mockImplementation((payload) => new Promise(resolve => { finishSave = () => resolve({ config: payload }); }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
  const login = await screen.findByRole('button', { name: 'Log in to library' });
  fireEvent.click(screen.getByRole('button', { name: 'Disable AI' }));
  expect(login.disabled).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  expect(login.disabled).toBe(true);
  fireEvent.click(login);
  expect(api.runUniversityLogin).not.toHaveBeenCalled();
  await act(async () => { finishSave(); });
  await waitFor(() => expect(login.disabled).toBe(false));
  expect(api.runUniversityLogin).not.toHaveBeenCalled();
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
