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
import { fetchReview } from '../api/reviewApi.js';
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: { ready: true } }) }));
vi.mock('../components/CollectionPicker.jsx', () => ({ default: () => null }));

import Today from './Today.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it.each([
  ['Add to library', { added: 1, pending_sync: 1 }, /Zotero sync pending \(1\); not saved to Zotero yet/],
  ['Trash', { trashed: 1, marked_read_error: 'locked' }, /Zotero mark-read failed/],
])('Spot-check %s keeps a visible warning when Zotero side effects fail', async (button, response, warning) => {
  daily.fetchDailySlate.mockResolvedValue({ papers: [], awaiting_review_total: 0 });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  fetchReview.mockResolvedValue({ items: [{ id: 5, stable_feed_key: 'feed:g:spot',
    title: 'Spot-check paper', review_ready: true }] });
  daily.addToLibrary.mockResolvedValue(response);
  daily.trashPapers.mockResolvedValue(response);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);
  await screen.findByText('Spot-check paper');
  fireEvent.click(screen.getByRole('button', { name: button }));
  const message = await screen.findByText(warning);
  expect(message.closest('[role="status"]').className).toContain('bg-amber-50');
  client.clear();
});

it('selects only the visible source-filtered slate for batch actions', async () => {
  daily.fetchDailySlate.mockResolvedValue({ papers: [
    { item_id: 1, item_key: 'P1', stable_feed_key: 'feed:P1', title: 'arXiv paper', feed_name: 'arXiv', review_ready: true },
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

it('blocks unreviewed Add but keeps Trash available', async () => {
  daily.fetchDailySlate.mockResolvedValue({ papers: [
    { item_id: 3, item_key: 'P3', stable_feed_key: 'feed:P3', title: 'Not reviewed', review_ready: false },
  ], awaiting_review_total: 1 });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  daily.trashPapers.mockResolvedValue({ trashed: 1 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);
  await screen.findByText('Not reviewed');
  fireEvent.click(screen.getByLabelText('Select all'));
  expect(screen.getByRole('button', { name: 'Add 1 to library' }).disabled).toBe(true);
  expect(screen.getByText('Generate a review before adding.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Trash 1' }));
  await waitFor(() => expect(daily.trashPapers.mock.calls[0][0]).toEqual([3]));
  client.clear();
});
