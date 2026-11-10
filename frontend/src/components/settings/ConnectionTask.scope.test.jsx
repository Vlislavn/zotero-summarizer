// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import ConnectionTask from './ConnectionTask.jsx';

const api = vi.hoisted(() => ({ fetchConnections: vi.fn(), registerConnection: vi.fn(), listConnectionModels: vi.fn(),
  previewConnection: vi.fn(), applyConnection: vi.fn(), readConnection: vi.fn(), testConnection: vi.fn() }));
vi.mock('../../api/connectionsApi.js', () => api);
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function fixture() {
  const providers = [{ name: 'local', type: 'openai', base_url: 'http://127.0.0.1:9000/v1', auth_mode: 'none' },
    { name: 'hosted-review', type: 'openai', base_url: 'https://example.invalid/v1', auth_mode: 'legacy' },
    { name: 'hosted-fast', type: 'openai', base_url: 'https://example.invalid/v1', auth_mode: 'legacy' }];
  return { llm_routing: { providers, default: { provider: 'local', model: 'local-model' },
    feed: { provider: 'hosted-fast', model: 'screen-model' }, backlog: { provider: 'hosted-fast', model: 'backlog-model' },
    deep_review: { provider: 'hosted-review', model: 'review-model' } } };
}
function show() {
  const config = fixture();
  api.fetchConnections.mockResolvedValue({ revision: 'r1', config, providers: config.llm_routing.providers });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConnectionTask /></QueryClientProvider>);
  return { config, client };
}
it('shows real task models and distinguishable saved connections without sending anything', async () => {
  show();
  const usage = await screen.findByRole('region', { name: 'Model for each step' });
  expect(within(usage).getByText('review-model')).toBeTruthy();
  expect(within(usage).getByText('screen-model')).toBeTruthy();
  expect(within(usage).getByText('local-model')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Change Paper reviews & questions' }));
  const select = screen.getByRole('combobox', { name: 'Saved provider' });
  expect(select.value).toBe('hosted-review');
  expect(within(select).getByRole('option', { name: /hosted-review.*Hosted/ })).toBeTruthy();
  expect(within(select).getByRole('option', { name: /hosted-fast.*Hosted/ })).toBeTruthy();
  expect(screen.getByText(/Currently saved model: review-model/)).toBeTruthy();
  expect(api.registerConnection).not.toHaveBeenCalled(); expect(api.testConnection).not.toHaveBeenCalled();
});
it('applies a chosen model to the named task while retaining the default and other explicit routes', async () => {
  const { config } = show();
  fireEvent.click(await screen.findByRole('button', { name: 'Change Paper reviews & questions' }));
  const provider = config.llm_routing.providers[1];
  api.registerConnection.mockImplementation(async command => ({ operation_id: command.operation_id, revision: 'r1',
    provider, state: 'registered_inactive', reused: true }));
  api.listConnectionModels.mockResolvedValue({ catalog: 'returned', models: ['chosen-model'], revision: 'r1' });
  api.previewConnection.mockImplementation(async command => ({ revision: 'r1', preview: 'preview', ready_to_apply: true,
    impact: { targets: command.targets, default: { before: config.llm_routing.default, after: config.llm_routing.default },
      stages: ['feed', 'backlog', 'deep_review'].map(stage => ({ stage, before: config.llm_routing[stage],
        after: stage === 'deep_review' ? { provider: provider.name, model: command.model } : config.llm_routing[stage] })),
      telegram: {}, shared_authorization: { consumers: [] } } }));
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
  fireEvent.change(await screen.findByRole('combobox', { name: 'Model' }), { target: { value: 'chosen-model' } });
  await waitFor(() => expect(api.previewConnection).toHaveBeenLastCalledWith(expect.objectContaining({ model: 'chosen-model', targets: ['deep_review'] })));
  expect(screen.getByRole('checkbox', { name: /Paper reviews & questions/ }).checked).toBe(true);
  expect(screen.getByRole('checkbox', { name: /Fallback \(steps without their own model\)/ }).checked).toBe(false);
  expect(api.applyConnection).not.toHaveBeenCalled();
  let saved;
  api.applyConnection.mockImplementation(async command => {
    saved = { ...config, llm_routing: { ...config.llm_routing, deep_review: { provider: provider.name, model: command.model } } };
    api.fetchConnections.mockResolvedValue({ revision: 'r2', config: saved, providers: saved.llm_routing.providers });
    return { state: 'selection_saved', selection_matches: true, revision: 'r2', provider, config: saved };
  });
  fireEvent.click(await screen.findByRole('button', { name: 'Use for selected tasks' }));
  await screen.findByText(/Selection saved/);
  expect(api.applyConnection).toHaveBeenCalledWith(expect.objectContaining({ targets: ['deep_review'], model: 'chosen-model' }), expect.objectContaining({ targets: ['deep_review'] }));
  expect(saved.llm_routing.default).toEqual(config.llm_routing.default);
  expect(saved.llm_routing.feed).toEqual(config.llm_routing.feed);
  expect(saved.llm_routing.backlog).toEqual(config.llm_routing.backlog);
  expect(api.testConnection).not.toHaveBeenCalled();
  api.testConnection.mockResolvedValue({ revision: 'r2', current: true, status: 'disabled', targets: ['deep_review'], stages: [], selections: {} });
  fireEvent.click(screen.getByRole('button', { name: 'Test selected tasks' }));
  await waitFor(() => expect(api.testConnection).toHaveBeenCalledWith({ revision: 'r2', stages: ['deep_review'] }));
});
it('retains unresolved model ownership across changing and cancelling a connection draft', async () => {
  show();
  api.testConnection.mockResolvedValue({ revision: 'r1', current: true, status: 'fail', attempted: true,
    provider: 'local', model: 'local-model', ownership: 'unresolved', hold: true });
  fireEvent.click(await screen.findByRole('button', { name: 'Test saved model' }));
  await screen.findByText(/The request may still be running/);
  fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel', exact: true }));
  expect(screen.getByText(/The request may still be running/)).toBeTruthy();
  expect(screen.getAllByRole('button', { name: /^Test/ }).every(button => button.disabled)).toBe(true);
  expect(api.testConnection).toHaveBeenCalledTimes(1);
});
it('tests only the explicitly named saved task and keeps identity and local refusal visible', async () => {
  show();
  api.testConnection.mockResolvedValue({ revision: 'r1', current: true, status: 'degraded', attempted: false,
    targets: ['deep_review'], selections: { deep_review: { provider: 'hosted-review', model: 'review-model', base_url: 'https://example.invalid/v1' } },
    stages: [{ stage: 'deep_review', provider: 'hosted-review', model: 'review-model', status: 'fail', attempted: false,
      hardware_safe: 'unknown', reason_code: 'effective_envelope_unknown', detail: 'Synthetic missing runtime evidence' }] });
  fireEvent.click(await screen.findByRole('button', { name: 'Test Paper reviews & questions' }));
  const row = await screen.findByRole('listitem', { name: 'Paper reviews & questions' });
  expect(await within(row).findByText('Test not sent')).toBeTruthy();
  expect(api.testConnection).toHaveBeenCalledWith({ revision: 'r1', stages: ['deep_review'] });
  expect(within(row).getByText('Synthetic missing runtime evidence')).toBeTruthy();
  expect(within(row).getByText('review-model')).toBeTruthy();
  expect(api.registerConnection).not.toHaveBeenCalled();
});
