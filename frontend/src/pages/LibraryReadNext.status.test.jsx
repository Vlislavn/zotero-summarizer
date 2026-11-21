// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import * as api from '../api/libraryApi.js';
import * as pending from '../api/pendingApi.js';
import LibraryReadNext from './LibraryReadNext.jsx';
import { useReviewCoolLoop } from '../hooks/useReviewCoolLoop.js';

vi.mock('../api/libraryApi.js');
vi.mock('../api/pendingApi.js');
vi.mock('../hooks/useServerAvailable.js', () => ({ default: () => true }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: { zotero: { db_found: true } } }) }));
vi.mock('../hooks/useReviewCoolLoop.js', () => ({ useReviewCoolLoop: vi.fn() }));
vi.mock('../offlineStore.js', async (original) => ({ ...(await original()), allPapers: async () => [] }));

const NOW = new Date().toISOString();
const row = (n, score) => ({ item_key: `K${n}`, title: `Paper ${n}`, ...(score == null ? {} : { relevance_score: score }) });
const flush = () => act(async () => {});
const mount = async (entry = '/library') => {
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={[entry]}><LibraryReadNext /></MemoryRouter></QueryClientProvider>);
  await flush();
};
const status = () => screen.getByTestId('library-status');

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
  useReviewCoolLoop.mockReturnValue({ autoReview: {}, coolUndecided: 0 });
  api.fetchCollections.mockResolvedValue({ items: [] });
  api.fetchTags.mockResolvedValue({ items: [] });
  pending.fetchPendingCount.mockResolvedValue(0);
  api.fetchReadingQueue.mockResolvedValue({ items: [row(1, 4.5)], status: 'ready', model_ready: true, computed_at: NOW });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('counts «not scored» over the whole queue, not over the filtered rows', async () => {
  api.fetchReadingQueue.mockResolvedValue({
    items: [row(1, 4.5), row(2), row(3), row(4)], status: 'ready', model_ready: true, computed_at: NOW,
  });
  await mount('/library?b=must_read');

  expect(screen.getByText('Paper 1')).toBeTruthy();
  expect(screen.queryByText('Paper 2')).toBeNull();  // the filter hides the unscored rows
  expect(status().textContent).toMatch(/3 not scored/);
});

it('shows one status line for a failed read: the error as an alert, one Retry, the old banners gone', async () => {
  api.fetchReadingQueue.mockRejectedValue(Object.assign(new Error('boom'), { status: 500 }));
  await mount();

  expect(screen.getAllByRole('alert')).toHaveLength(1);
  expect(status().textContent).toMatch(/^Error:\s*Couldn’t load your list/);
  expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(1);
  expect(screen.queryByTestId('library-empty')).toBeNull();  // no «nothing here» next to an error
  api.fetchReadingQueue.mockResolvedValue({ items: [row(1, 4)], status: 'ready', model_ready: true, computed_at: NOW });
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await flush();
  expect(screen.getByText('Paper 1')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('says «Scoring your library…» as one status while scoring runs', async () => {
  api.fetchReadingQueue.mockResolvedValue({ items: [row(1, 4)], status: 'computing', model_ready: true, computed_at: NOW });
  api.fetchReadingQueueStatus.mockResolvedValue({ running: true });
  await mount();

  expect(status().textContent).toMatch(/Scoring your library/);
  expect(screen.queryByText(/Scoring your whole library/)).toBeNull();
});

it('names the list that is empty after a search, with its one action', async () => {
  api.fetchReadingQueue.mockResolvedValue({ items: [], status: 'ready', model_ready: true, computed_at: NOW });
  await mount();

  expect(screen.getByTestId('library-empty').getAttribute('data-state')).toBe('empty');
  expect(screen.getByRole('link', { name: 'Open Today' })).toBeTruthy();
});
