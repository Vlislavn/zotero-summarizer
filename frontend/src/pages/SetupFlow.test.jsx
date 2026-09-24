// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ fetchConfig: vi.fn(), updateConfig: vi.fn(), validateSetup: vi.fn() }));
vi.mock('../api/settingsApi.js', () => api);
vi.mock('../api/setupApi.js', () => ({ validateSetup: api.validateSetup }));
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
  Object.values(api).forEach((mock) => mock.mockReset());
  api.fetchConfig.mockResolvedValue(config);
  api.validateSetup.mockResolvedValue({ valid: true, field_errors: [] });
  api.updateConfig.mockImplementation(async (payload) => ({ config: payload }));
});
afterEach(cleanup);

it('completes ML-only setup after validation and saves the personalized goal', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);

  await screen.findByText('Zotero step');
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.click(screen.getByLabelText(/ML-only triage/));
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
  client.clear();
});

it('returns to the research step on validation errors and does not save invalid setup', async () => {
  api.validateSetup.mockResolvedValue({ valid: false, field_errors: [
    { field: 'research_goals', message: 'Add a research goal.' },
  ] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SetupFlow /></MemoryRouter></QueryClientProvider>);

  await screen.findByText('Zotero step');
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.click(screen.getByLabelText(/ML-only triage/));
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  fireEvent.change(screen.getByLabelText('Research goal'), { target: { value: 'Valid-looking goal' } });
  fireEvent.click(screen.getByRole('button', { name: 'Finish' }));

  expect(await screen.findByText('Add a research goal.')).toBeTruthy();
  expect(api.validateSetup).toHaveBeenCalledTimes(1);
  expect(api.updateConfig).not.toHaveBeenCalled();
  client.clear();
});
