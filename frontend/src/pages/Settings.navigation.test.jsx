// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { Link, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderRouter } from '../testRouter.jsx';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(), fetchUniversityAccessStatus: vi.fn(), runUniversityLogin: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/libraryApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null, pillars: {}, subsystemIssues: [], isLoading: true }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
import Settings from './Settings.jsx';
import ConfigTask from '../components/settings/ConfigTask.jsx';

const original = { _revision: 'r1', llm_enabled: true, research_goals: ['Saved goal'], triage_criteria: ['Evidence'], output_language: 'English',
  university_access: { enabled: true, login_url: 'https://saved.invalid/login', browser_profile: 'retained' },
  llm_routing: { providers: [{ name: 'saved', type: 'openai', base_url: 'http://127.0.0.1:9999/v1', auth_mode: 'none', temperature: 0.7 }],
    default: { provider: 'saved', model: 'saved-model' }, feed: { model: 'exception' } }, prestige: { extra: 'retained' } };
let client;
function mount(path = '/settings') {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return renderRouter(<><nav><Link to="/elsewhere">Elsewhere</Link></nav><Routes>
    <Route path="/settings" element={<Settings />} />
    <Route path="/today" element={<ConfigTask task="selection" backTo="/today" backLabel="Back to Today" />} />
    <Route path="/elsewhere" element={<h1>Elsewhere</h1>} />
  </Routes></>, client, [path]);
}
beforeEach(() => {
  api.fetchConfig.mockResolvedValue(structuredClone(original));
  api.updateConfig.mockImplementation(async payload => ({ status: 'ok', config: { ...payload, _revision: 'r2' } }));
  api.fetchUniversityAccessStatus.mockResolvedValue({ browser_available: true, login_url: 'https://old-status.invalid/login' });
  vi.spyOn(window, 'confirm').mockReturnValue(false);
});
afterEach(() => { cleanup(); client?.clear(); vi.restoreAllMocks(); vi.resetAllMocks(); });

it('root has saved summaries and wayfinding, not mounted independent editors or a save-all', async () => {
  mount(); await screen.findByRole('link', { name: 'Choose models' });
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Verify setup' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Article selection → Today' }).getAttribute('href')).toBe('/today?task=selection');
  expect(screen.getByRole('link', { name: 'Telegram sources → Weekly' }).getAttribute('href')).toBe('/weekly?task=telegram');
  expect(api.updateConfig).not.toHaveBeenCalled();
});
it.each([
  [{ model: 'exception' }, 'exception'],
  [{ provider: 'saved' }, 'saved-model'],
  [{ provider: null, model: null }, 'saved-model'],
])('summary identifies effective task models rather than only whether an override exists (%j)', async (feed, expected) => {
  api.fetchConfig.mockResolvedValue({ ...original, llm_routing: { ...original.llm_routing, feed } });
  mount();
  const screening = await screen.findByRole('listitem', { name: 'New paper screening' });
  expect(screening.textContent).toContain(expected);
  expect(screening.textContent).toContain('saved');
  expect(screen.getByRole('listitem', { name: 'Paper reviews & questions' }).textContent).toContain('saved-model');
  expect(screen.queryByRole('combobox')).toBeNull();
  expect(api.updateConfig).not.toHaveBeenCalled();
});
it('one mode task saves only its scope, preserving current connection/options and all hidden fields', async () => {
  mount('/settings?task=mode&keep=one');
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Enable AI-assisted features' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save AI preference' }));
  await screen.findByText('AI-assisted features saved.');
  expect(api.updateConfig.mock.calls[0][0]).toEqual({ ...original, llm_enabled: false });
  expect(screen.getByRole('link', { name: 'Back to Settings' }).getAttribute('href')).toBe('/settings?keep=one');
});
it('same-path task navigation, history and unload retain the exact draft until explicit discard', async () => {
  const { router } = mount('/settings');
  fireEvent.click(await screen.findByRole('link', { name: 'Change output language' }));
  const field = await screen.findByRole('textbox', { name: 'Output language' });
  fireEvent.change(field, { target: { value: 'Local draft' } });
  await act(async () => router.navigate('/settings?task=mode'));
  expect(screen.getByRole('textbox', { name: 'Output language' }).value).toBe('Local draft');
  await act(async () => router.navigate(-1));
  expect(screen.getByRole('textbox', { name: 'Output language' }).value).toBe('Local draft');
  const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  window.confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('link', { name: 'Elsewhere' }));
  await screen.findByRole('heading', { name: 'Elsewhere' });
  expect(api.updateConfig).not.toHaveBeenCalled();
});
it('late acknowledgement advances the submitted baseline but never erases newer typed text', async () => {
  let finish; api.updateConfig.mockImplementation(payload => new Promise(resolve => { finish = () => resolve({ status: 'ok', config: { ...payload, _revision: 'r2' } }); }));
  mount('/settings?task=language'); const field = await screen.findByRole('textbox', { name: 'Output language' });
  fireEvent.change(field, { target: { value: 'Submitted language' } }); fireEvent.click(screen.getByRole('button', { name: 'Save output language' }));
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  fireEvent.change(field, { target: { value: 'Newer draft' } });
  await act(async () => finish());
  expect(field.value).toBe('Newer draft'); expect(screen.getByRole('button', { name: 'Save output language' }).disabled).toBe(false);
  expect(client.getQueryData(['runtime-config']).output_language).toBe('Submitted language');
});
it('lost/malformed ACK retains the draft and requires readback plus visible owned-value review, never replay', async () => {
  api.updateConfig.mockResolvedValue({});
  mount('/settings?task=language'); const field = await screen.findByRole('textbox', { name: 'Output language' });
  fireEvent.change(field, { target: { value: 'Local draft' } }); fireEvent.click(screen.getByRole('button', { name: 'Save output language' }));
  await screen.findByRole('alert'); expect(field.value).toBe('Local draft');
  expect(screen.getByRole('button', { name: 'Save output language' }).disabled).toBe(true);
  api.fetchConfig.mockResolvedValue({ ...original, _revision: 'r2', output_language: 'Concurrent saved value' });
  fireEvent.click(screen.getByRole('button', { name: 'Read saved values; keep draft' }));
  await screen.findByRole('region', { name: 'Review changed saved values' });
  expect(screen.getByText('Saved now: Concurrent saved value')).toBeTruthy(); expect(field.value).toBe('Local draft');
  expect(api.updateConfig).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Keep my draft for these saved values' }));
  expect(screen.getByRole('button', { name: 'Save output language' }).disabled).toBe(false);
  api.updateConfig.mockImplementation(async payload => ({ status: 'ok', config: { ...payload, _revision: 'r3' } }));
  fireEvent.click(screen.getByRole('button', { name: 'Save output language' })); await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(2));
  expect(api.updateConfig.mock.lastCall[0]._revision).toBe('r2');
});
it('a background read never stamps a dirty draft with its fresh revision', async () => {
  mount('/today?task=selection'); const field = await screen.findByRole('textbox', { name: 'Goal 1' });
  fireEvent.change(field, { target: { value: 'Retained draft' } });
  await act(async () => client.setQueryData(['runtime-config'], { ...original, _revision: 'r9', llm_routing: { ...original.llm_routing, default: { provider: 'saved', model: 'new-model' } } }));
  fireEvent.click(screen.getByRole('button', { name: 'Save article selection' }));
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  expect(api.updateConfig.mock.calls[0][0]._revision).toBe('r1');
  expect(api.updateConfig.mock.calls[0][0].research_goals).toEqual(['Retained draft']);
});
it('pending-only exit stays protected; leaving never cancels the request or lets a late ACK redirect/replace newer saved state', async () => {
  let finish;
  api.updateConfig.mockImplementation(payload => new Promise(resolve => { finish = () => resolve({ status: 'ok', config: { ...payload, _revision: 'r2' } }); }));
  const { router } = mount('/settings?task=language'); const field = await screen.findByRole('textbox', { name: 'Output language' });
  fireEvent.change(field, { target: { value: 'Submitted language' } }); fireEvent.click(screen.getByRole('button', { name: 'Save output language' }));
  await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
  fireEvent.change(field, { target: { value: 'English' } });
  fireEvent.click(screen.getByRole('link', { name: 'Elsewhere' })); expect(field.value).toBe('English');
  const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  window.confirm.mockReturnValue(true); fireEvent.click(screen.getByRole('link', { name: 'Elsewhere' })); await screen.findByRole('heading', { name: 'Elsewhere' });
  const newer = { ...original, _revision: 'r9', output_language: 'Newer saved language' };
  await act(async () => client.setQueryData(['runtime-config'], newer));
  await act(async () => finish()); await waitFor(() => expect(client.getMutationCache().findAll({ status: 'pending' })).toHaveLength(0));
  expect(router.state.location.pathname).toBe('/elsewhere'); expect(client.getQueryData(['runtime-config'])).toEqual(newer);
  expect(api.updateConfig).toHaveBeenCalledTimes(1);
});
it('matching legacy aliases cannot retire a dirty canonical editor without admission', async () => {
  const { router } = mount('/today?task=selection');
  const field = await screen.findByRole('textbox', { name: 'Goal 1' });
  fireEvent.change(field, { target: { value: 'Exact unsaved alias draft' } });
  await act(async () => router.navigate('/settings#essentials-goals'));
  expect(router.state.location.pathname).toBe('/today');
  expect(screen.getByRole('textbox', { name: 'Goal 1' })).toBe(field);
  expect(field.value).toBe('Exact unsaved alias draft');
  expect(window.confirm).toHaveBeenCalledTimes(1);
  await act(async () => router.navigate('/today?task=selection&keep=anchor#details'));
  expect(screen.getByRole('textbox', { name: 'Goal 1' })).toBe(field);
  expect(window.confirm).toHaveBeenCalledTimes(1);
  window.confirm.mockReturnValue(true);
  await act(async () => router.navigate('/settings#essentials-goals'));
  const replacement = await screen.findByRole('textbox', { name: 'Goal 1' });
  expect(replacement).not.toBe(field);
  expect(replacement.value).toBe('Saved goal');
  expect(router.state.location.pathname).toBe('/today');
  expect(window.confirm).toHaveBeenCalledTimes(2);
  await act(async () => router.navigate('/settings#essentials-goals'));
  expect(screen.getByRole('textbox', { name: 'Goal 1' }).value).toBe('Saved goal');
  expect(window.confirm).toHaveBeenCalledTimes(2);
  expect(api.updateConfig).not.toHaveBeenCalled();
});
it.each([[false, false], [false, true], [true, false], [true, true]])(
  'explicit read retains typing during GET (initial draft=%s, saved owned value changed=%s)', async (initiallyDirty, ownedChanged) => {
    mount('/settings?task=language');
    const field = await screen.findByRole('textbox', { name: 'Output language' });
    if (initiallyDirty) fireEvent.change(field, { target: { value: 'Initial draft' } });
    const saved = { ...original, _revision: 'r2', output_language: ownedChanged ? 'French' : 'English' };
    await act(async () => client.setQueryData(['runtime-config'], saved));
    let release;
    api.fetchConfig.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve(saved); }));
    fireEvent.click(await screen.findByRole('button', { name: 'Read saved values; keep draft' }));
    await waitFor(() => expect(release).toBeTypeOf('function'));
    fireEvent.change(field, { target: { value: 'Typed during the read' } });
    await act(async () => release());
    expect(field.value).toBe('Typed during the read');
    expect(api.updateConfig).not.toHaveBeenCalled();
    if (ownedChanged) {
      await screen.findByRole('region', { name: 'Review changed saved values' });
      expect(screen.getByText('Saved now: French')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Use saved values instead' }));
      expect(field.value).toBe('French');
      expect(screen.getByRole('button', { name: 'Save output language' }).disabled).toBe(true);
    } else {
      expect(screen.getByRole('button', { name: 'Save output language' }).disabled).toBe(false);
      fireEvent.click(screen.getByRole('button', { name: 'Save output language' }));
      await waitFor(() => expect(api.updateConfig).toHaveBeenCalledTimes(1));
      expect(api.updateConfig.mock.calls[0][0]).toEqual({ ...saved, output_language: 'Typed during the read' });
    }
  });

it('clean return restores its named Settings launcher, with no discard confirmation', async () => {
  mount(); fireEvent.click(await screen.findByRole('link', { name: 'Change output language' }));
  await screen.findByRole('textbox', { name: 'Output language' });
  fireEvent.click(screen.getByRole('link', { name: 'Back to Settings' }));
  const launcher = await screen.findByRole('link', { name: 'Change output language' }); await waitFor(() => expect(document.activeElement).toBe(launcher));
  expect(window.confirm).not.toHaveBeenCalled(); expect(api.updateConfig).not.toHaveBeenCalled();
});
