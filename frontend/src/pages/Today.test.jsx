// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const daily = vi.hoisted(() => ({
  fetchDailySlate: vi.fn(), addToLibrary: vi.fn(), trashPapers: vi.fn(),
  triggerTriageBacklog: vi.fn(), getTriageStatus: vi.fn(),
}));
vi.mock('../api/dailyApi.js', () => daily);
vi.mock('../api/reviewApi.js', () => ({ fetchReview: vi.fn() }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: { ready: true } }) }));
vi.mock('../components/CollectionPicker.jsx', () => ({ default: () => null }));

import Today from './Today.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('selects only the visible source-filtered slate for batch actions', async () => {
  daily.fetchDailySlate.mockResolvedValue({ papers: [
    { item_id: 1, item_key: 'P1', stable_feed_key: 'feed:P1', title: 'arXiv paper', feed_name: 'arXiv' },
    { item_id: 2, item_key: 'P2', stable_feed_key: 'feed:P2', title: 'PubMed paper', feed_name: 'PubMed' },
  ], awaiting_review_total: 2 });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  daily.addToLibrary.mockResolvedValue({ added: 1, pending_sync: 1 });
  daily.trashPapers.mockResolvedValue({ trashed: 0 });
  daily.triggerTriageBacklog.mockResolvedValue({ started: true });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);

  await screen.findByText('arXiv paper');
  fireEvent.change(screen.getByTitle('Show only papers from one feed source'), { target: { value: 'arXiv' } });
  fireEvent.click(screen.getByLabelText('Select all'));
  expect(screen.getByRole('button', { name: 'Add 1 to library' }).disabled).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Add 1 to library' }));

  await waitFor(() => expect(daily.addToLibrary).toHaveBeenCalledWith([1], ''));
  expect(await screen.findByText(/Zotero sync pending \(1\)/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Add to library' }).disabled).toBe(true);
  client.clear();
});
