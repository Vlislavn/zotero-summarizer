// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import Weekly from './Weekly.jsx';
import { researchFeedApi } from '../api/researchFeedApi.js';

vi.mock('../api/researchFeedApi.js', () => ({ researchFeedApi: {
  list: vi.fn(), get: vi.fn(), download: (id, format) => `/download/${id}.${format}`,
} }));
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

function mount(path = '/weekly') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Weekly /></MemoryRouter></QueryClientProvider>);
}
function report() {
  return {
    metadata: { from: '2026-08-20', to: '2026-08-29', source: 'app_rss', counts: { shortlisted: 2, discovered: 4 } },
    cards: [{ candidate: { source_id: 'feed:one', title: 'Agent benchmark', url: 'https://example.test/paper' },
      triage: { matched_projects: ['agent harness'], rationale: 'Tests current harness failure recovery' },
      card: { worth_reading: 'unknown', problem: 'Recovery', core_idea: 'Replay controlled failures', engineering_novelty: 'Frozen traces',
        project_use_notes: ['Replace the harness retry baseline, compare recovery on held-out traces.'],
        code_urls: ['https://example.test/code'], dataset_urls: [], model_urls: [],
        reproducibility_tier: 'unknown', reproducibility_rationale: 'Effort not assessed',
        missing_reproduction_inputs: ['Trace release missing'], evidence_gaps: ['No clinical evaluation'] },
      provenance: { basis: 'full_text', model: null, generated_at: null } }],
    rejected_near_threshold: [{ candidate: { source_id: 'two', title: 'Attractive but irrelevant', url: 'https://example.test/reject' }, triage: { rationale: 'No declared project match' } }],
    manual_full_text_required: [{ source_id: 'feed:three', title: 'Missing current review' }],
    failed: [{ source_id: 'feed:four', error: 'Deep review timed out' }],
    research_ideas_by_project: { 'agent harness': [{ proposed_change: 'Replace retry control', source_paper_id: 'feed:one', minimal_validation_experiment: 'Compare recovery on frozen traces' }] },
  };
}

it('opens a retained CLI run with cards, paper links, downloads and honest unknowns', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [{ run_id: 'weekly-cli', available: true, counts: { cards_generated: 1 } }] });
  researchFeedApi.get.mockResolvedValue(report());
  mount('/weekly?run=weekly-cli');

  await screen.findByRole('heading', { name: 'Agent benchmark' });
  expect(screen.getByLabelText('Saved digest').value).toBe('weekly-cli');
  expect(researchFeedApi.get).toHaveBeenCalledWith('weekly-cli');
  expect(screen.getByText('Tests current harness failure recovery')).toBeTruthy();
  expect(screen.getByText(/Replace the harness retry baseline/)).toBeTruthy();
  expect(screen.getAllByText(/None verified — availability unknown/)).toHaveLength(2);
  expect(screen.getByText('Trace release missing')).toBeTruthy();
  expect(screen.getByText('No clinical evaluation')).toBeTruthy();
  expect(screen.getByText(/model: unrecorded/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Open paper review' }).getAttribute('href')).toBe('/paper/feed%3Aone?autoRun=false');
  expect(screen.getByRole('link', { name: 'Missing current review' }).getAttribute('href')).toBe('/paper/feed%3Athree?autoRun=false');
  expect(screen.getByRole('link', { name: 'feed:four' }).getAttribute('href')).toBe('/paper/feed%3Afour?autoRun=false');
  expect(screen.getByRole('link', { name: 'Download canonical JSON' }).getAttribute('href')).toBe('/download/weekly-cli.json');
  expect(screen.getByRole('link', { name: 'Download Markdown' }).getAttribute('href')).toBe('/download/weekly-cli.md');
  expect(screen.getByRole('link', { name: 'https://example.test/code' }).getAttribute('rel')).toBe('noopener noreferrer');
  expect(screen.getByText(/No declared project match/)).toBeTruthy();
  expect(screen.getByText(/Minimal validation: Compare recovery on frozen traces/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Build/ })).toBeNull();
});

it('does not replace the selected run when an older detail request completes late', async () => {
  let resolveOld;
  researchFeedApi.list.mockResolvedValue({ items: ['weekly-old', 'weekly-new'].map((run_id) => ({ run_id, available: true, counts: { cards_generated: 1 } })) });
  researchFeedApi.get.mockImplementation((id) => id === 'weekly-old'
    ? new Promise((resolve) => { resolveOld = resolve; }) : Promise.resolve(report()));
  mount('/weekly?run=weekly-old');
  await waitFor(() => expect(researchFeedApi.get).toHaveBeenCalledWith('weekly-old'));
  await screen.findByRole('option', { name: 'weekly-new · 1 cards' });
  fireEvent.change(screen.getByLabelText('Saved digest'), { target: { value: 'weekly-new' } });
  await screen.findByRole('heading', { name: 'Agent benchmark' });
  const stale = report(); stale.cards[0].candidate.title = 'Older report title';
  await act(async () => resolveOld(stale));
  expect(screen.queryByRole('heading', { name: 'Older report title' })).toBeNull();
  expect(screen.getByLabelText('Saved digest').value).toBe('weekly-new');
});

it('distinguishes a valid empty digest, missing reviews and per-paper failures', async () => {
  const empty = report(); empty.cards = []; empty.research_ideas_by_project = {};
  researchFeedApi.list.mockResolvedValue({ items: [] }); researchFeedApi.get.mockResolvedValue(empty);
  mount('/weekly?run=weekly-empty');
  await screen.findByText(/No engineering cards in this run/);
  expect(screen.getByRole('link', { name: 'Missing current review' })).toBeTruthy();
  expect(screen.getByText(/does not prove the PDF is unavailable/)).toBeTruthy();
  expect(screen.getByText('No structured ideas recorded.')).toBeTruthy();
  expect(screen.getByText(/Deep review timed out/)).toBeTruthy();
});

it('renders missing legacy provenance and idea groups without inventing assessments', async () => {
  const legacy = report(); delete legacy.cards[0].provenance; delete legacy.research_ideas_by_project;
  researchFeedApi.list.mockResolvedValue({ items: [] }); researchFeedApi.get.mockResolvedValue(legacy);
  mount('/weekly?run=weekly-legacy');
  await screen.findByRole('heading', { name: 'Agent benchmark' });
  expect(screen.getByText(/Source basis: unknown · model: unrecorded/)).toBeTruthy();
  expect(screen.getByText('No structured ideas recorded.')).toBeTruthy();
});

it('keeps an unavailable report an error with explicit refresh recovery, not an empty success', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [{ run_id: 'weekly-bad', available: false }] });
  researchFeedApi.get.mockRejectedValueOnce(new Error('Saved weekly digest is unreadable or unsupported'));
  mount('/weekly?run=weekly-bad');
  await screen.findByRole('alert');
  expect(screen.queryByText(/No engineering cards in this run/)).toBeNull();
  expect(screen.queryByRole('link', { name: 'Download canonical JSON' })).toBeNull();
  researchFeedApi.get.mockResolvedValueOnce(report());
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved runs' }));
  await screen.findByRole('heading', { name: 'Agent benchmark' });
});

it('keeps a failed refresh from displaying an old success under an error', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [] }); researchFeedApi.get.mockResolvedValueOnce(report());
  mount('/weekly?run=weekly-removed');
  await screen.findByRole('heading', { name: 'Agent benchmark' });
  researchFeedApi.get.mockRejectedValueOnce(new Error('Weekly digest not found'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved runs' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('heading', { name: 'Agent benchmark' })).toBeNull();
});

it('attempts local reads even when network is offline and exposes server failure', async () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  researchFeedApi.list.mockRejectedValue(new Error('Server unavailable'));
  researchFeedApi.get.mockRejectedValue(new Error('Server unavailable'));
  mount('/weekly?run=weekly-offline');
  await screen.findAllByRole('alert');
  expect(researchFeedApi.get).toHaveBeenCalledWith('weekly-offline');
  expect(screen.getByText(/not cached for offline use/)).toBeTruthy();
  expect(screen.queryByText(/No engineering cards/)).toBeNull();
});

it('never turns an unsafe source or artifact URL into an active link', async () => {
  const unsafe = report();
  unsafe.cards[0].candidate.url = 'javascript:alert(1)';
  unsafe.cards[0].card.code_urls = ['data:text/html,unsafe'];
  researchFeedApi.list.mockResolvedValue({ items: [] }); researchFeedApi.get.mockResolvedValue(unsafe);
  mount('/weekly?run=weekly-unsafe');
  await screen.findByRole('heading', { name: 'Agent benchmark' });
  expect(screen.queryByRole('link', { name: 'Original source' })).toBeNull();
  expect(screen.getByText('data:text/html,unsafe').tagName).toBe('SPAN');
});
