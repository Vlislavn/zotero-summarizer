// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(), fetchDoctorStatus: vi.fn(),
  runDoctor: vi.fn(), cancelDoctor: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/setupApi.js', () => api);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null }) }));
vi.mock('../components/settings/ReadinessStrip.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/AiModelsSection.jsx', () => ({ default: ({ enabled, onEnabledChange }) => (
  <button type="button" onClick={() => onEnabledChange(!enabled)}>{enabled ? 'Disable AI' : 'Enable AI'}</button>
)}));
vi.mock('../components/settings/EssentialsSection.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/UniversityAccessPanel.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/CalibrationCard.jsx', () => ({ default: () => null }));
vi.mock('../components/settings/RssFeedsSection.jsx', () => ({ default: () => null }));
import Settings from './Settings.jsx';

const configA = {
  llm_enabled: true, research_goals: ['Clinical agents'], triage_criteria: ['Reproducibility'], output_language: 'English',
  llm_routing: { providers: [], default: {}, feed: {}, backlog: {}, deep_review: {} },
};
const statusA = { ready: false, status: 'not_started', config_signature: 'A', checks: [], modes: {} };
const readyA = { ...statusA, ready: true, status: 'ready' };
const statusB = { ...statusA, config_signature: 'B', checks: [
  { id: 'dry_run', status: 'not_started', message: 'Configuration B needs verification' },
] };
let client;

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function mount() {
  client = new QueryClient({ defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false },
  } });
  return render(<QueryClientProvider client={client}><Settings /></QueryClientProvider>);
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  window.history.replaceState(null, '', '/settings');
  api.fetchConfig.mockResolvedValue(configA);
  api.updateConfig.mockImplementation(async (config) => ({ config }));
  api.fetchDoctorStatus.mockResolvedValue(statusA);
});
afterEach(() => { cleanup(); client?.clear(); });

it('cannot publish delayed verification A after Save B and its newer Doctor status', async () => {
  const verificationA = deferred();
  api.runDoctor.mockReturnValueOnce(verificationA.promise);
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Verify setup' }));
  await waitFor(() => expect(api.runDoctor).toHaveBeenCalledWith(null));

  fireEvent.click(screen.getByRole('button', { name: 'Disable AI' }));
  expect(screen.getByRole('button', { name: 'Save changes' }).disabled).toBe(false);
  api.fetchDoctorStatus.mockResolvedValue(statusB);
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await screen.findByText('Configuration B needs verification');
  expect(api.updateConfig.mock.calls[0][0].llm_enabled).toBe(false);
  expect(client.getQueryData(['setup-doctor'])).toEqual(statusB);

  await act(async () => { verificationA.resolve(readyA); await verificationA.promise; });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Verify setup' }).disabled).toBe(false));
  expect(client.getQueryData(['setup-doctor'])).toEqual(statusB);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();

  const readyB = { ...statusB, ready: true, status: 'ready', checks: [] };
  api.runDoctor.mockImplementationOnce(async () => {
    api.fetchDoctorStatus.mockResolvedValue(readyB);
    return readyB;
  });
  fireEvent.click(screen.getByRole('button', { name: 'Verify setup' }));
  await screen.findByText('✓ Setup verified');
  expect(client.getQueryData(['setup-doctor'])).toEqual(readyB);
});

it('retires a pending verification when a newer status read has replaced it', async () => {
  const verificationA = deferred();
  api.runDoctor.mockReturnValueOnce(verificationA.promise);
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Verify setup' }));
  await waitFor(() => expect(api.runDoctor).toHaveBeenCalledTimes(1));
  const latest = { ...statusA, status: 'needs_action', checks: [
    { id: 'rss_source', status: 'needs_action', message: 'Latest status: no enabled source' },
  ] };
  api.fetchDoctorStatus.mockResolvedValue(latest);
  await act(async () => { await client.refetchQueries({ queryKey: ['setup-doctor'] }); });
  await screen.findByText('Latest status: no enabled source');

  await act(async () => { verificationA.resolve(readyA); await verificationA.promise; });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Verify setup' }).disabled).toBe(false));
  expect(client.getQueryData(['setup-doctor'])).toEqual(latest);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
});

it('retires an old status read on Save even if its transport ignores cancellation', async () => {
  const readA = deferred();
  api.fetchDoctorStatus.mockReturnValueOnce(readA.promise).mockResolvedValue(statusB);
  mount();
  await waitFor(() => expect(api.fetchDoctorStatus).toHaveBeenCalledTimes(1));
  const oldSignal = api.fetchDoctorStatus.mock.calls[0][0].signal;
  fireEvent.click(screen.getByRole('button', { name: 'Disable AI' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await screen.findByText('Configuration B needs verification');
  expect(oldSignal.aborted).toBe(true);

  await act(async () => { readA.resolve(readyA); await readA.promise; });
  expect(client.getQueryData(['setup-doctor'])).toEqual(statusB);
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  expect(api.runDoctor).not.toHaveBeenCalled();
});

it.each([undefined, 503, 404, 403, 422])('handles config-write uncertainty without hiding status %s or repeating the write', async (status) => {
  const uncertain = ![403, 422].includes(status);
  api.fetchDoctorStatus.mockResolvedValue(readyA);
  api.updateConfig.mockImplementation(async () => {
    if (uncertain) api.fetchDoctorStatus.mockResolvedValue(statusB);
    throw Object.assign(new Error('Config acknowledgement unavailable'), { status });
  });
  mount();
  await screen.findByText('✓ Setup verified');
  fireEvent.click(screen.getByRole('button', { name: 'Disable AI' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await screen.findByText(/Save failed: Config acknowledgement unavailable/);
  if (uncertain) {
    await screen.findByText('Configuration B needs verification');
    expect(screen.queryByText('✓ Setup verified')).toBeNull();
  } else {
    expect(screen.getByText('✓ Setup verified')).toBeTruthy();
  }
  expect(client.getQueryData(['setup-doctor'])).toEqual(uncertain ? statusB : readyA);
  expect(api.fetchDoctorStatus).toHaveBeenCalledTimes(uncertain ? 2 : 1);
  expect(api.updateConfig).toHaveBeenCalledTimes(1);
  expect(api.updateConfig.mock.calls[0][0].llm_enabled).toBe(false);
  expect(screen.getByRole('button', { name: 'Enable AI' })).toBeTruthy();
  expect(screen.queryByText('Saved successfully')).toBeNull();
  expect(api.runDoctor).not.toHaveBeenCalled();
});

it('shows the supplied pending-check reason without claiming verification or running a check', async () => {
  api.fetchDoctorStatus.mockResolvedValue(statusB);
  mount();
  await screen.findByText('Configuration B needs verification');
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
  expect(screen.getByRole('button', { name: 'Verify setup' }).disabled).toBe(false);
  expect(api.runDoctor).not.toHaveBeenCalled();
});

it('folds multiple pending checks without losing their identifiers or supplied reasons', async () => {
  api.fetchDoctorStatus.mockResolvedValue({ ...statusA, checks: [
    { id: 'runtime_model', status: 'not_started', message: 'Not checked yet' },
    { id: 'dry_run', status: 'not_started', message: 'Waiting for explicit verification' },
  ] });
  mount();
  const summary = await screen.findByText('2 checks not started');
  const disclosure = summary.closest('details');
  expect(disclosure.open).toBe(false);
  expect(disclosure.textContent).toContain('runtime model');
  expect(disclosure.textContent).toContain('dry run');
  expect(disclosure.textContent).toContain('Not checked yet');
  expect(disclosure.textContent).toContain('Waiting for explicit verification');
  expect(api.runDoctor).not.toHaveBeenCalled();
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
});

it('projects unknown load admission from its structured receipt without inferring from prose', async () => {
  api.fetchDoctorStatus.mockResolvedValue({ ...statusA, status: 'needs_action', checks: [
    { id: 'llm_inference', status: 'needs_action', message: 'Synthetic result',
      load_receipts: [{ admission: { status: 'unknown' }, attempted: false }],
      recovery: { action: 'open_ai_settings', label: 'Review endpoint settings' } },
  ] });
  mount();
  await screen.findByText('Local load safety is not established.');
  expect(screen.getByRole('link', { name: 'Review endpoint settings' }).getAttribute('href')).toBe('/settings#ai-models');
  expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  expect(api.runDoctor).not.toHaveBeenCalled();
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
});

it('stops only the current run and rereads unresolved ownership without accepting Ready', async () => {
  const running = { ...statusA, status: 'running', run_id: 'owned-running-doctor', attempt: { ownership: 'active' } };
  const unresolved = { ...running, status: 'unavailable', attempt: { ownership: 'unresolved' } };
  let health = running;
  api.fetchDoctorStatus.mockImplementation(async () => health);
  mount();
  const stop = await screen.findByRole('button', { name: 'Stop verification' });
  api.cancelDoctor.mockImplementation(async () => {
    health = unresolved;
    return { ...running, cancellation_requested: true };
  });
  fireEvent.click(stop);
  await waitFor(() => expect(api.cancelDoctor).toHaveBeenCalledOnce());
  expect(api.cancelDoctor.mock.calls[0][0]).toBe(running.run_id);
  await screen.findByText('Verification stopped. Callback or remote termination is not established; no Ready result was accepted.');
  expect(client.getQueryData(['setup-doctor'])).toEqual(unresolved);
  expect(api.fetchDoctorStatus.mock.calls.length).toBeGreaterThan(1);
  expect(api.runDoctor).not.toHaveBeenCalled();
  expect(screen.queryByText('✓ Setup verified')).toBeNull();
});
