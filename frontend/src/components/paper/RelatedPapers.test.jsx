// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
