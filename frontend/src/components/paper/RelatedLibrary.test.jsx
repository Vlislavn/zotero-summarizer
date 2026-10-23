// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import RelatedLibrary from './RelatedLibrary.jsx';
import channelCandidates from './fixtures/discoveryChannelCandidates.json';

const api = vi.hoisted(() => ({ fetchRelatedPapers: vi.fn(), discoverRelatedPapers: vi.fn() }));
vi.mock('../../api/libraryApi.js', () => api);
const clients = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; vi.restoreAllMocks(); });
beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
const comments = { availability: 'available', app_available: true, native_available: true, records: [
  { id: 'native:n1', text: 'Не подходит. Not useful.', source: 'native_note', authorship: 'uncertain', revision: 'clock' },
] };
const result = { saved_seed: true, status: 'ready', items: [{ item_key: 'PAPER002', title: 'Saved, already read paper',
  relationship: { sentence: 'Both metadata records mention “graph retrieval”; this is not confirmation or contradiction.' },
  evidence: { seed: { field: 'abstract', start: 0, end: 15, excerpt: 'Graph retrieval' },
    candidate: { field: 'title', start: 0, end: 15, excerpt: 'Graph retrieval' } },
  source_basis: 'abstract_only', comments,
}], seed_comments: { availability: 'absent', records: [], app_available: true, native_available: true } };
function mount(detail = { item_key: 'PAPER001' }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const open = vi.fn();
  const element = (current) => <QueryClientProvider client={client}><MemoryRouter>
    <RelatedLibrary key={current.item_key} itemKey={current.item_key} detail={current} onOpenPaper={open} />
  </MemoryRouter></QueryClientProvider>;
  return { ...render(element(detail)), element, open };
}

it('links at most three saved matches with separate exact evidence and uncertain comment authorship', async () => {
  api.fetchRelatedPapers.mockResolvedValue({ ...result, items: [1, 2, 3, 4].map((id) => ({ ...result.items[0],
    item_key: `PAPER00${id}`, title: `Saved paper ${id}` })) });
  const { open } = mount();
  await screen.findByRole('heading', { name: 'Related in my library' });
  await screen.findByText('Saved paper 1');
  expect(screen.getAllByRole('link')).toHaveLength(3);
  fireEvent.click(screen.getByRole('link', { name: 'Saved paper 1' }));
  expect(open).toHaveBeenCalledWith('PAPER001');
  expect(screen.getAllByText(/authorship uncertain/)).toHaveLength(3);
  expect(screen.getAllByText('Не подходит. Not useful.')).toHaveLength(3);
  expect(screen.getAllByText(/Public metadata only/)).toHaveLength(3);
  expect(api.discoverRelatedPapers).not.toHaveBeenCalled();
});

it('starts discovery only on click, labels unknown membership, and never offers Add or auto-review', async () => {
  api.fetchRelatedPapers.mockResolvedValue(result);
  api.discoverRelatedPapers.mockResolvedValue({ candidates: [
    { candidate_id: 'new', title: 'Old but new to me', year: 1995, in_library: false, url: 'https://example.org/paper' },
    { candidate_id: 'unknown', title: 'Unresolved paper', in_library: null },
  ] });
  mount();
  const find = await screen.findByRole('button', { name: 'Find new relevant' });
  expect(api.discoverRelatedPapers).not.toHaveBeenCalled();
  fireEvent.click(find);
  await screen.findByText('Old but new to me');
  expect(api.discoverRelatedPapers).toHaveBeenCalledWith('PAPER001', { refresh: false });
  expect(screen.getByText(/membership unknown — not confirmed new/)).toBeTruthy();
  expect(screen.getByText(/nothing added or reviewed/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Add to library' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Generate review' })).toBeNull();
});

it('distinguishes loading, empty, unavailable, stale/offline and retryable discovery failures', async () => {
  let complete;
  api.fetchRelatedPapers.mockReturnValue(new Promise((resolve) => { complete = resolve; }));
  api.discoverRelatedPapers.mockRejectedValue(new Error('provider unavailable'));
  mount();
  expect(screen.getByText('Finding related saved papers…')).toBeTruthy();
  await act(async () => complete({ ...result, status: 'empty', items: [] }));
  await screen.findByText('No related saved papers returned by this library search.');
  fireEvent.click(screen.getByRole('button', { name: 'Find new relevant' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('alert').textContent).toContain('provider unavailable');
  api.fetchRelatedPapers.mockResolvedValue({ ...result, status: 'unavailable', items: [], offline: true });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh related' }));
  await screen.findByText(/Library search unavailable/);
  expect(screen.getByText(/Offline or stale snapshot/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Find new relevant' }).disabled).toBe(true);
});

it('refetches after a saved-comment update without claiming old discovery reflects the new context', async () => {
  api.fetchRelatedPapers.mockResolvedValue(result);
  api.discoverRelatedPapers.mockResolvedValue({ candidates: [{ candidate_id: 'old', title: 'Old result' }] });
  const { rerender, element } = mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Find new relevant' }));
  await screen.findByText('Old result');
  rerender(element({ item_key: 'PAPER001', user_note: 'Edited' }));
  await waitFor(() => expect(api.fetchRelatedPapers).toHaveBeenCalledTimes(2));
  expect(screen.queryByText('Old result')).toBeNull();
});

it('does not offer discovery for unsaved Today seeds and keeps review-independent failures local', async () => {
  api.fetchRelatedPapers.mockRejectedValue(new Error('local corpus unavailable'));
  mount();
  await screen.findByRole('alert');
  expect(screen.getByRole('alert').textContent).toContain('Your review is still usable');
  expect(screen.queryByRole('button', { name: 'Find new relevant' })).toBeNull();
});

it('does not present an unavailable semantic comment-ranker as a working option', async () => {
  api.fetchRelatedPapers.mockResolvedValue(result);
  mount();
  await screen.findByText('Saved, already read paper');
  expect(api.fetchRelatedPapers).toHaveBeenLastCalledWith('PAPER001');
  expect(screen.getByText(/semantic intent is unqualified/)).toBeTruthy();
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(api.discoverRelatedPapers).not.toHaveBeenCalled();
});

it('shows partial provider failure separately from empty and retries through the same explicit action', async () => {
  api.fetchRelatedPapers.mockResolvedValue(result);
  api.discoverRelatedPapers.mockResolvedValue({ candidates: [], discovery: {
    provider_availability: 'partial_failure', source_outcomes: [
      { provider: 'arxiv', variant: 0, status: 'success_empty', count: 0, error_code: null },
      { provider: 'semantic_scholar', variant: 0, status: 'failed', count: null, error_code: 'rate_limited' },
    ],
  } });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Find new relevant' }));
  await screen.findByText(/Partial provider failure — available results are retained/);
  expect(screen.getByText(/arxiv · pass 1 · success empty · 0 returned/)).toBeTruthy();
  expect(screen.getByText(/semantic_scholar · pass 1 · failed · rate limited/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Find new relevant' }));
  await waitFor(() => expect(api.discoverRelatedPapers).toHaveBeenLastCalledWith('PAPER001', { refresh: true }));
});

it('opens identifier-only results from actual channel-shaped fixtures without inventing title URLs', async () => {
  api.fetchRelatedPapers.mockResolvedValue(result);
  api.discoverRelatedPapers.mockResolvedValue({ candidates: [
    ...channelCandidates,
    { candidate_id: 'unsafe-with-doi', title: 'Unsafe URL with a valid identifier fallback',
      url: 'javascript:alert(1)', doi: '10.1234/safe-fallback' },
    { candidate_id: 'unsupported', title: 'No supported identifier or address', url: 'data:text/html,unsafe',
      pmid: '../not-a-pmid', openalex_id: 'https://example.org/W123' },
  ] });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Find new relevant' }));

  const doi = await screen.findByRole('link', { name: channelCandidates[0].title });
  expect(doi.getAttribute('href')).toBe('https://doi.org/10.1234/fixture-doi');
  expect(screen.getByRole('link', { name: channelCandidates[1].title }).getAttribute('href'))
    .toBe('https://pubmed.ncbi.nlm.nih.gov/12345678/');
  expect(screen.getByRole('link', { name: channelCandidates[2].title }).getAttribute('href'))
    .toBe('https://openalex.org/W1234567890');
  expect(screen.getByRole('link', { name: 'Unsafe URL with a valid identifier fallback' }).getAttribute('href'))
    .toBe('https://doi.org/10.1234/safe-fallback');
  expect(screen.queryByRole('link', { name: 'No supported identifier or address' })).toBeNull();
  expect(screen.getByText('No supported link available.')).toBeTruthy();
  expect([...document.querySelectorAll('a[target="_blank"]')].every((link) => /^https?:\/\//.test(link.getAttribute('href')))).toBe(true);
  expect(screen.queryByRole('button', { name: 'Add to library' })).toBeNull();
});
