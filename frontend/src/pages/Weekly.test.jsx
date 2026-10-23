// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import Weekly from './Weekly.jsx';
import { researchFeedApi } from '../api/researchFeedApi.js';

vi.mock('../api/researchFeedApi.js', () => ({ researchFeedApi: {
  list: vi.fn(), get: vi.fn(), run: vi.fn(), download: (id, format) => `/download/${id}.${format}`,
} }));
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

function mount(path = '/weekly') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Weekly /></MemoryRouter></QueryClientProvider>);
}
function report() {
  return {
    metadata: { from: '2026-08-20', to: '2026-08-29', source: 'app_rss', counts: { shortlisted: 2, discovered: 4 }, ranking: { contract: 'weekly_rank_v1' } },
    cards: [{ candidate: { source_id: 'feed:one', title: 'Agent benchmark', url: 'https://example.test/paper' },
      triage: { matched_projects: ['agent harness'], rationale: 'Tests the current harness failure recovery' },
      card: { worth_reading: 'skim', problem: 'Recovery', core_idea: 'Replay controlled failures', engineering_novelty: 'Frozen traces',
        project_use_notes: ['Replace the harness retry baseline, compare recovery on held-out traces.'],
        code_urls: ['javascript:alert(1)'], dataset_urls: [], model_urls: [],
        reproducibility_tier: 'unknown', reproducibility_rationale: 'Effort not assessed',
        missing_reproduction_inputs: ['Trace release missing'], evidence_gaps: ['No clinical evaluation'] },
      provenance: { basis: 'full_text', model: null, generated_at: null } }],
    rejected_near_threshold: [{ candidate: { source_id: 'two', title: 'Attractive but irrelevant', url: 'https://example.test/reject' }, triage: { rationale: 'No declared project match' } }],
    manual_full_text_required: [{ source_id: 'feed:three', title: 'Missing current review' }],
    failed: [{ source_id: 'four', error: 'ValueError: ranking_input_invalid' }],
    research_ideas_by_project: { 'agent harness': [{ proposed_change: 'Replace retry control', source_paper_id: 'feed:one', minimal_validation_experiment: 'Compare recovery on frozen traces' }] },
  };
}

it('builds through the cached service and opens actionable saved cards with honest unknowns', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [] });
  researchFeedApi.run.mockResolvedValue({ run_id: 'weekly-new' });
  researchFeedApi.get.mockResolvedValue(report());
  mount();
  await screen.findByText(/No saved weekly digests/);
  expect(researchFeedApi.run).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('From (UTC)'), { target: { value: '2026-08-20' } });
  fireEvent.change(screen.getByLabelText('To (inclusive, UTC)'), { target: { value: '2026-08-29' } });
  fireEvent.change(screen.getByLabelText('Venue (optional)'), { target: { value: 'ICML 2026' } });
  fireEvent.click(screen.getByRole('button', { name: 'Build from saved reviews' }));

  await screen.findByRole('heading', { name: 'Agent benchmark' });
  expect(researchFeedApi.run).toHaveBeenCalledTimes(1);
  expect(researchFeedApi.run.mock.calls[0][0]).toEqual({ start: '2026-08-20', end: '2026-08-29', venue: 'ICML 2026', card_budget: 10 });
  expect(researchFeedApi.get).toHaveBeenCalledWith('weekly-new');
  expect(screen.getByText(/zotero-summarizer research-feed run/).textContent).toContain("--venue 'ICML 2026'");
  expect(screen.getByText('Tests the current harness failure recovery')).toBeTruthy();
  expect(screen.getByText(/Replace the harness retry baseline/)).toBeTruthy();
  expect(screen.getAllByText(/None verified — availability unknown/)).toHaveLength(2);
  expect(screen.getByText('Trace release missing')).toBeTruthy();
  expect(screen.getByText('No clinical evaluation')).toBeTruthy();
  expect(screen.getByText(/model: unrecorded/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Open paper review' }).getAttribute('href')).toBe('/paper/feed%3Aone');
  expect(screen.getByRole('link', { name: 'Missing current review' }).getAttribute('href')).toBe('/paper/feed%3Athree');
  expect(screen.getByRole('link', { name: 'Download canonical JSON' }).getAttribute('href')).toBe('/download/weekly-new.json');
  expect(screen.getByText('javascript:alert(1)').tagName).toBe('SPAN');
  expect(screen.getByText(/No declared project match/)).toBeTruthy();
  expect(screen.getByText(/ranking_input_invalid/)).toBeTruthy();
  expect(screen.getByText(/Minimal validation: Compare recovery on frozen traces/)).toBeTruthy();
});

it('opens a retained CLI run without starting another run', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [{ run_id: 'weekly-cli', available: true, counts: { cards_generated: 1 } }] });
  researchFeedApi.get.mockResolvedValue(report());
  mount('/weekly?run=weekly-cli');
  await screen.findByRole('heading', { name: 'Agent benchmark' });
  expect(screen.getByLabelText('Saved digest').value).toBe('weekly-cli');
  expect(screen.getByRole('combobox', { name: 'Saved digest', exact: true }).value).toBe('weekly-cli');
  expect(researchFeedApi.run).not.toHaveBeenCalled();
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

it('distinguishes a valid empty digest, missing current reviews and per-paper failures', async () => {
  const empty = report(); empty.cards = []; empty.research_ideas_by_project = {};
  researchFeedApi.list.mockResolvedValue({ items: [] }); researchFeedApi.get.mockResolvedValue(empty);
  mount('/weekly?run=weekly-empty');
  await screen.findByText(/No engineering cards in this run/);
  expect(screen.getByRole('link', { name: 'Missing current review' })).toBeTruthy();
  expect(screen.getByText(/does not prove the PDF is unavailable/)).toBeTruthy();
  expect(screen.getByText('No structured ideas recorded.')).toBeTruthy();
});

it('keeps unqualified reviews distinct from known manual full-text needs', async () => {
  const pending = report();
  pending.cards = []; pending.research_ideas_by_project = {}; pending.manual_full_text_required = [];
  pending.unqualified_reviews = [{ candidate: { source_id: 'feed:pending', title: 'Review provenance pending' }, reason: 'review_missing' }];
  researchFeedApi.list.mockResolvedValue({ items: [] }); researchFeedApi.get.mockResolvedValue(pending);
  mount('/weekly?run=weekly-pending');
  await screen.findByText(/No engineering cards in this run/);
  expect(screen.getByRole('heading', { name: 'Pending review qualification' })).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Review provenance pending' }).getAttribute('href')).toBe('/paper/feed%3Apending');
  expect(screen.getByText('review missing')).toBeTruthy();
  expect(screen.getByText(/does not establish that its PDF is unavailable/)).toBeTruthy();
  expect(researchFeedApi.run).not.toHaveBeenCalled();
});

it('keeps an unavailable report an error with explicit refresh recovery, not an empty success', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [{ run_id: 'weekly-bad', available: false }] });
  researchFeedApi.get.mockRejectedValueOnce(new Error('Saved weekly digest is unreadable or unsupported'));
  mount('/weekly?run=weekly-bad');
  await screen.findByRole('alert');
  expect(screen.queryByText(/No engineering cards in this run/)).toBeNull();
  researchFeedApi.get.mockResolvedValueOnce(report());
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved runs' }));
  await screen.findByRole('heading', { name: 'Agent benchmark' });
});

it('disables server-only builds when the device goes offline without starting work on reconnect', async () => {
  researchFeedApi.list.mockResolvedValue({ items: [] });
  const online = vi.spyOn(navigator, 'onLine', 'get');
  online.mockReturnValue(true);
  mount();
  await screen.findByText(/No saved weekly digests/);
  online.mockReturnValue(false);
  act(() => window.dispatchEvent(new Event('offline')));
  expect(screen.getByRole('button', { name: 'Build from saved reviews' }).disabled).toBe(true);
  expect(screen.getByText(/weekly reports are not cached on this device/)).toBeTruthy();
  online.mockReturnValue(true);
  act(() => window.dispatchEvent(new Event('online')));
  expect(screen.getByRole('button', { name: 'Build from saved reviews' }).disabled).toBe(false);
  expect(researchFeedApi.run).not.toHaveBeenCalled();
});

it('disables duplicate builds and preserves honest uncertain completion after transport failure', async () => {
  let rejectRun;
  researchFeedApi.list.mockResolvedValue({ items: [] });
  researchFeedApi.run.mockImplementation(() => new Promise((_, reject) => { rejectRun = reject; }));
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Build from saved reviews' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Building from saved reviews…' }).disabled).toBe(true));
  rejectRun(new Error('Server unavailable'));
  await screen.findByRole('alert');
  expect(screen.getByText(/No completion was confirmed/)).toBeTruthy();
  expect(researchFeedApi.run).toHaveBeenCalledTimes(1);
});
