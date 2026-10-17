// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { savePapers } from '../offlineStore.js';
import LibraryReadNext from './LibraryReadNext.jsx';

vi.mock('../hooks/useReviewCoolLoop.js', () => ({
  useReviewCoolLoop: () => ({ autoReview: {}, coolUndecided: 0 }),
}));
vi.mock('../components/library/ReadNextView.jsx', () => ({
  default: ({ items }) => <ul>{items.map(item => <li key={item.item_key}>{item.title}</li>)}</ul>,
}));
let client;
afterEach(() => {
  cleanup();
  client?.clear();
  onlineManager.setOnline(true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('opens the durable working set on a cold offline mount while setup health is paused', async () => {
  await savePapers([{ item_key: 'CACHED_QUEUE', title: 'Previously synced paper' }]);
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  onlineManager.setOnline(false);
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Synthetic Mac off')));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><MemoryRouter><LibraryReadNext /></MemoryRouter></QueryClientProvider>);

  expect(await screen.findByText('Previously synced paper')).toBeTruthy();
  expect(client.getQueryState(['setup-status']).fetchStatus).toBe('paused');
  expect(screen.getByText(/Cached working set/)).toBeTruthy();
  expect(screen.getByPlaceholderText(/Meaning search needs the Mac/).disabled).toBe(true);
  expect(fetch.mock.calls.some(([path]) => path.startsWith('/api/library/reading-queue?'))).toBe(true);
  expect(fetch.mock.calls.some(([path]) => path.startsWith('/api/setup'))).toBe(false);
});
