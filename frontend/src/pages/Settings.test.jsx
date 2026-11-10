// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderRouter } from '../testRouter.jsx';
const api = vi.hoisted(() => Object.fromEntries(['fetchConfig', 'updateConfig', 'fetchDoctorStatus', 'runDoctor', 'cancelDoctor',
  'fetchUniversityAccessStatus', 'runUniversityLogin', 'fetchRssFeeds', 'addRssFeed', 'updateRssFeed', 'deleteRssFeed',
  'importRssFeedsFromZotero', 'refreshRssFeeds'].map(name => [name, vi.fn()])));
const conn = vi.hoisted(() => ({ fetchConnections: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/libraryApi.js', () => api);
vi.mock('../api/setupApi.js', () => api);
vi.mock('../api/connectionsApi.js', async importOriginal => ({ ...await importOriginal(), ...conn }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
import Settings from './Settings.jsx';
import { RssSourcesTask } from '../components/settings/SourceTasks.jsx';
import { DoctorChecklist } from '../components/settings/DeploymentCard.jsx';
const config = { _revision: 'r1', llm_enabled: true, research_goals: ['Clinical agents'], triage_criteria: ['Evidence quality'], output_language: 'English',
  llm_routing: { providers: [], default: {}, feed: {}, backlog: {}, deep_review: {} }, prestige: { weight: 0.15, custom_venues: ['Example Journal'] } };
let client;
function mount(path) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return renderRouter(<Routes><Route path="/settings" element={<Settings />} /><Route path="/ops" element={<DoctorChecklist />} />
    <Route path="/today" element={<RssSourcesTask />} /></Routes>, client, [path]);
}
beforeEach(() => {
  Object.values(api).forEach(mock => mock.mockReset()); conn.fetchConnections.mockReset();
  api.fetchConfig.mockResolvedValue(config);
  api.updateConfig.mockImplementation(async payload => ({ status: 'ok', config: { ...payload, _revision: 'r2' } }));
  conn.fetchConnections.mockResolvedValue({ revision: 'r1', config, providers: [] });
  api.fetchRssFeeds.mockResolvedValue({ feeds: [] });
  api.fetchUniversityAccessStatus.mockResolvedValue({ browser_available: true, login_url: '' });
  api.runUniversityLogin.mockReturnValue(new Promise(() => {}));
  api.fetchDoctorStatus.mockResolvedValue({ ready: false, status: 'needs_action', checks: [
    { id: 'llm_inference', status: 'needs_action', message: 'Review the configured endpoint', recovery: { action: 'open_ai_settings', label: 'Review endpoint settings' } },
    { id: 'rss_source', status: 'needs_action', message: 'No enabled source', recovery: { action: 'open_sources_settings', label: 'Add a source in Settings' } },
  ] });
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('No unplanned transport is permitted')));
});
afterEach(() => { cleanup(); client?.clear(); vi.unstubAllGlobals(); });
it('typed recovery opens the canonical AI task without saving, enabling AI or submitting inference', async () => {
  const { router } = mount('/ops?tab=system&task=readiness');
  fireEvent.click(await screen.findByRole('link', { name: 'Review endpoint settings' }));
  await screen.findByRole('button', { name: 'Add provider' });
  expect(router.state.location.pathname + router.state.location.search).toBe('/settings?task=connection');
  expect(api.runDoctor).not.toHaveBeenCalled(); expect(api.updateConfig).not.toHaveBeenCalled(); expect(fetch.mock.calls.every(([url]) => /llm-reachability|step-history/.test(url))).toBe(true);
});
it('source recovery focuses its canonical task without saving, refreshing or mounting a form for each row', async () => {
  mount('/ops?tab=system&task=readiness');
  const recovery = await screen.findByRole('link', { name: 'Add a source in Settings' }); expect(recovery.getAttribute('href')).toBe('/today?task=rss');
  fireEvent.click(recovery); const heading = await screen.findByRole('heading', { level: 1, name: 'RSS sources' });
  await waitFor(() => expect(document.activeElement).toBe(heading)); await screen.findByText('No RSS feeds saved.');
  expect(screen.queryByRole('textbox')).toBeNull(); expect(api.runDoctor).not.toHaveBeenCalled();
  expect(api.updateConfig).not.toHaveBeenCalled(); expect(api.refreshRssFeeds).not.toHaveBeenCalled();
});
it('wizard source hash resolves to the same task while preserving unrelated query context', async () => {
  const { router } = mount('/settings?keep=one#rss-feeds');
  const heading = await screen.findByRole('heading', { level: 1, name: 'RSS sources' });
  await waitFor(() => expect(document.activeElement).toBe(heading));
  expect(router.state.location.pathname + router.state.location.search).toBe('/today?keep=one&task=rss');
  expect(api.updateConfig).not.toHaveBeenCalled(); expect(api.refreshRssFeeds).not.toHaveBeenCalled();
});
it('wizard AI hash resolves to the same connection task without automatic effects', async () => {
  const { router } = mount('/settings#ai-models'); await screen.findByRole('button', { name: 'Add provider' });
  expect(router.state.location.search).toBe('?task=connection'); expect(api.updateConfig).not.toHaveBeenCalled(); expect(fetch.mock.calls.every(([url]) => /llm-reachability|step-history/.test(url))).toBe(true);
});
it('saves an ML-only preference while round-tripping unsurfaced configuration', async () => {
  mount('/settings?task=mode'); client.setQueryData(['setup-doctor'], { ready: true });
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Enable AI-assisted features' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save AI preference' })); await screen.findByText('AI-assisted features saved.');
  expect(api.updateConfig).toHaveBeenCalledTimes(1); expect(api.updateConfig.mock.calls[0][0]).toEqual({ ...config, llm_enabled: false });
  expect(client.getQueryData(['setup-doctor'])?.ready).toBe(false);
});
it('admits library login only after the edited URL is authoritatively saved, not from stale readiness', async () => {
  let finishSave;
  api.updateConfig.mockImplementation(payload => new Promise(resolve => { finishSave = () => resolve({ status: 'ok', config: { ...payload, _revision: 'r2' } }); }));
  mount('/settings?task=university'); const url = await screen.findByRole('textbox', { name: 'Library login URL' });
  const login = screen.getByRole('button', { name: 'Log in to library' }); expect(login.disabled).toBe(true);
  fireEvent.change(url, { target: { value: 'https://synthetic-university.invalid/login' } }); fireEvent.click(login); expect(api.runUniversityLogin).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Save library access' })); await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  expect(login.disabled).toBe(true); expect(api.runUniversityLogin).not.toHaveBeenCalled(); await act(async () => finishSave());
  await waitFor(() => expect(login.disabled).toBe(false));
  expect(client.getQueryData(['runtime-config']).university_access.login_url).toBe('https://synthetic-university.invalid/login');
  expect(client.getQueryData(['university-access-status']).login_url).toBe(''); fireEvent.click(login);
  await waitFor(() => expect(api.runUniversityLogin).toHaveBeenCalledTimes(1)); expect(api.updateConfig).toHaveBeenCalledTimes(1);
});
it.each(['ua_enabled', 'ua_ezproxy_prefix', 'ua_cookie_browser', 'ua_login_url'])(
  'blocks a failed or unconfirmed save of %s while keeping the edited destination visible', async field => {
    api.fetchConfig.mockResolvedValue({ ...config, university_access: { enabled: true, login_url: 'https://saved.invalid/login' } });
    api.updateConfig.mockRejectedValue(new TypeError('Save response lost')); mount('/settings?task=university');
    const url = await screen.findByRole('textbox', { name: 'Library login URL' }); const login = screen.getByRole('button', { name: 'Log in to library' });
    expect(login.disabled).toBe(false);
    if (field === 'ua_enabled') fireEvent.click(screen.getByRole('checkbox', { name: 'Enable browser PDF fetch for the review fleet' }));
    if (field === 'ua_ezproxy_prefix') fireEvent.change(screen.getByRole('textbox', { name: /EZproxy prefix/ }), { target: { value: 'https://proxy.invalid/?url=' } });
    if (field === 'ua_cookie_browser') fireEvent.change(screen.getByRole('combobox', { name: /Reuse an existing browser login/ }), { target: { value: 'chrome' } });
    if (field === 'ua_login_url') fireEvent.change(url, { target: { value: 'https://edited.invalid/login' } });
    expect(login.disabled).toBe(true); fireEvent.click(screen.getByRole('button', { name: 'Save library access' })); await screen.findByText(/Save failed:/);
    expect(login.disabled).toBe(true); expect(url.value).toBe(field === 'ua_login_url' ? 'https://edited.invalid/login' : 'https://saved.invalid/login');
    fireEvent.click(login); expect(api.runUniversityLogin).not.toHaveBeenCalled();
    expect(client.getQueryData(['runtime-config']).university_access.login_url).toBe('https://saved.invalid/login');
  });
it('keeps saved access unchanged across another task save, but blocks the access task’s own pending Save', async () => {
  let saved = { ...config, university_access: { login_url: 'https://saved.invalid/login' } };
  api.fetchConfig.mockImplementation(async () => saved);
  api.updateConfig.mockImplementation(async payload => { saved = { ...payload, _revision: 'r2' }; return { status: 'ok', config: saved }; });
  const { router } = mount('/settings?task=mode'); fireEvent.click(await screen.findByRole('checkbox', { name: 'Enable AI-assisted features' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save AI preference' })); await screen.findByText('AI-assisted features saved.');
  await act(async () => router.navigate('/settings?task=university'));
  const login = await screen.findByRole('button', { name: 'Log in to library' }); expect(login.disabled).toBe(false);
  let finishSave;
  api.updateConfig.mockImplementation(payload => new Promise(resolve => { finishSave = () => resolve({ status: 'ok', config: { ...payload, _revision: 'r3' } }); }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Library login URL' }), { target: { value: 'https://new.invalid/login' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save library access' })); await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(2));
  expect(login.disabled).toBe(true); fireEvent.click(login); expect(api.runUniversityLogin).not.toHaveBeenCalled();
  await act(async () => finishSave()); await waitFor(() => expect(login.disabled).toBe(false)); expect(api.runUniversityLogin).not.toHaveBeenCalled();
});
it('keeps edits visible and reports a failed save without a success message', async () => {
  api.updateConfig.mockRejectedValue(new Error('disk full')); mount('/settings?task=mode');
  const enabled = await screen.findByRole('checkbox', { name: 'Enable AI-assisted features' }); fireEvent.click(enabled);
  fireEvent.click(screen.getByRole('button', { name: 'Save AI preference' })); await screen.findByText(/Save failed:/);
  expect(enabled.checked).toBe(false); expect(screen.queryByText('AI-assisted features saved.')).toBeNull(); expect(api.updateConfig).toHaveBeenCalledTimes(1);
});
