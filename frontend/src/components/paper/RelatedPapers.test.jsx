// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import RelatedPapers from './RelatedPapers.jsx';
import { fetchRelatedPapers, assessRelatedPapers } from '../../api/libraryApi.js';

vi.mock('../../api/libraryApi.js', () => ({ fetchRelatedPapers: vi.fn(), assessRelatedPapers: vi.fn(),
  fetchDiscovery: async () => ({ admission: { state: 'unknown', basis: null }, context_fingerprint: null }),
  findNewRelevant: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });
const comments = { availability: 'empty', app_state: 'observed', native_state: 'observed', records: [] };
const baseline = {
  status: 'ready', context_fingerprint: 'a', comments_in_ranking: false, seed_comments: comments,
  boundary: { provider: 'configured-provider', model: 'configured-model', endpoint: 'http://localhost:1234/v1', enabled: true, token: 'token' },
  items: [{ item_key: 'GOOD0001', title: 'Saved read paper', relationship: 'Similarity, not verified.', source_basis: 'abstract_only', comments }],
};

function mount(key = 'SEED0001') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><MemoryRouter><RelatedPapers key={key} itemKey={key} /></MemoryRouter></QueryClientProvider>);
  return { client, ...view };
}

it('loads saved metadata only on request and requires consent to the actual processing boundary', async () => {
  fetchRelatedPapers.mockResolvedValue(baseline);
  assessRelatedPapers.mockResolvedValue({ ...baseline, assessment_status: 'assessed', comments_in_ranking: true });
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal('confirm', confirm);
  const { client } = mount();
  expect(fetchRelatedPapers).not.toHaveBeenCalled();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByRole('link', { name: 'Saved read paper' });
  expect(screen.getByRole('link', { name: 'Saved read paper' }).getAttribute('href')).toBe('/paper/GOOD0001');
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  const tryComments = screen.getByRole('button', { name: 'Try with comments — experimental' });
  fireEvent.click(tryComments);
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  expect(confirm.mock.calls[0][0]).toContain('configured-provider / configured-model (http://localhost:1234/v1)');
  expect(confirm.mock.calls[0][0]).toContain('complete private comments');
  fireEvent.click(tryComments);
  await waitFor(() => expect(assessRelatedPapers).toHaveBeenCalledWith('SEED0001', 'token', false));
  await screen.findByText(/Experimental comment-aware assessment/);
  client.clear();
});

it('retains three saved links, attributed comments and exact two-sided evidence only after consent', async () => {
  const native = { ...comments, availability: 'available', records: [
    { id: 'native:n1', text: 'Не подходит. Not useful.', source: 'native_note', authorship: 'uncertain' },
  ] };
  let current = { ...baseline, items: [2, 3, 4].map(n => ({ ...baseline.items[0], item_key: `PAPER00${n}`, title: `Saved paper ${n}`, comments: native })) };
  fetchRelatedPapers.mockImplementation(async () => current);
  assessRelatedPapers.mockImplementation(async () => {
    current = { ...current, assessment_status: 'assessed', comments_in_ranking: true, items: current.items.map(row => ({ ...row,
      used_context_ids: ['native:n1'], evidence: {
        seed: { item_key: 'SEED0001', field: 'abstract', start: 0, end: 15, excerpt: 'Graph retrieval' },
        candidate: { item_key: row.item_key, field: 'title', start: 0, end: row.title.length, excerpt: row.title },
      },
    })) };
    return current;
  });
  vi.stubGlobal('confirm', () => true);
  const { client } = mount();
  expect(fetchRelatedPapers).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByRole('link', { name: 'Saved paper 2' });
  expect(screen.getAllByRole('link').map(link => link.getAttribute('href'))).toEqual(['/paper/PAPER002', '/paper/PAPER003', '/paper/PAPER004']);
  expect(screen.getAllByText('Не подходит. Not useful.')).toHaveLength(3);
  expect(screen.getAllByText('native_note · uncertain · native:n1')).toHaveLength(3);
  expect(screen.queryByText('Metadata evidence')).toBeNull();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Try with comments — experimental' }));
  await screen.findByText(/Experimental comment-aware assessment/);
  expect(screen.getAllByText('seed · SEED0001 · abstract: “Graph retrieval”')).toHaveLength(3);
  current.items.forEach(row => expect(screen.getByText(`candidate · ${row.item_key} · title: “${row.title}”`)).toBeTruthy());
  expect(screen.getAllByText('Quote membership is checked; entailment is not independently verified.')).toHaveLength(3);
  expect(screen.getAllByText('native_note · uncertain · native:n1 · cited by assessment')).toHaveLength(3);
  expect(assessRelatedPapers).toHaveBeenCalledTimes(1);
  expect(assessRelatedPapers).toHaveBeenCalledWith('SEED0001', 'token', false);
  client.clear();
});

it('keeps observed empty and unavailable saved-library reads distinct from pending loading', async () => {
  let complete;
  fetchRelatedPapers.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  const { client } = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  expect(screen.getByText('Loading saved-library connections…')).toBeTruthy();
  await waitFor(() => expect(fetchRelatedPapers).toHaveBeenCalledTimes(1));
  await act(async () => complete({ ...baseline, status: 'empty', items: [] }));
  await screen.findByText('No related saved papers found in this scope.');
  expect(screen.queryByText('Loading saved-library connections…')).toBeNull();
  fetchRelatedPapers.mockResolvedValue({ ...baseline, status: 'unavailable', items: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved context' }));
  await screen.findByText('Saved-library search unavailable; check corpus/model availability.');
  expect(screen.queryByText('No related saved papers found in this scope.')).toBeNull();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  client.clear();
});

it('keeps an unavailable comment-assessment boundary non-operational without hiding metadata', async () => {
  fetchRelatedPapers.mockResolvedValue({ ...baseline, boundary: { ...baseline.boundary, enabled: false } });
  const confirm = vi.fn(() => true);
  vi.stubGlobal('confirm', confirm);
  const { client } = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByRole('link', { name: 'Saved read paper' });
  const button = screen.getByRole('button', { name: 'Try with comments — experimental' });
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(confirm).not.toHaveBeenCalled();
  expect(assessRelatedPapers).not.toHaveBeenCalled();
  expect(screen.queryByText(/Experimental comment-aware assessment/)).toBeNull();
  client.clear();
});

it.each(['failed', 'budget_exceeded', 'interrupted', 'stale'])('keeps %s honest without replacing useful metadata with empty success', async status => {
  fetchRelatedPapers.mockResolvedValue(baseline);
  assessRelatedPapers.mockResolvedValue({ ...baseline, assessment_status: status });
  vi.stubGlobal('confirm', () => true);
  const { client } = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Try with comments — experimental' }));
  await screen.findByText(new RegExp(`Comment assessment: ${status}`));
  expect(screen.getByRole('link', { name: 'Saved read paper' })).toBeTruthy();
  expect(screen.queryByText(/Experimental comment-aware assessment/)).toBeNull();
  client.clear();
});

it('refresh invalidates a previous assessment and retains short native negative comments as context', async () => {
  const native = { ...comments, availability: 'partial', native_state: 'failed', records: [
    { id: 'native-note', text: 'Нет. Not useful.', source: 'native_note', authorship: 'uncertain' },
  ] };
  fetchRelatedPapers.mockResolvedValue({ ...baseline, seed_comments: native });
  assessRelatedPapers.mockResolvedValue({ ...baseline, assessment_status: 'assessed', comments_in_ranking: true });
  vi.stubGlobal('confirm', () => true);
  const { client } = mount();
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  await screen.findByText('Нет. Not useful.');
  expect(screen.getByText(/App: observed; native: failed/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Try with comments — experimental' }));
  await screen.findByText(/Experimental comment-aware assessment/);
  fetchRelatedPapers.mockResolvedValue({ ...baseline, context_fingerprint: 'changed' });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved context' }));
  await waitFor(() => expect(screen.queryByText(/Experimental comment-aware assessment/)).toBeNull());
  expect(screen.getByRole('link', { name: 'Saved read paper' })).toBeTruthy();
  client.clear();
});
