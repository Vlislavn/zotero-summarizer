// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { applyPull } from '../offlineStore.js';
import LibraryReadNext from './LibraryReadNext.jsx';

const api = vi.hoisted(() => ({ fetchReadingQueue: vi.fn(), fetchReadingQueueStatus: vi.fn(), available: false }));
vi.mock('../api/libraryApi.js', () => ({
  ...api, fetchCollections: vi.fn(), fetchTags: vi.fn(), startTriage: vi.fn(),
  addItemToCollection: vi.fn(), fetchFulltext: vi.fn(),
  fetchFulltextStatus: vi.fn(), syncRelTags: vi.fn(), syncScoreRanks: vi.fn(),
}));
vi.mock('../hooks/useServerAvailable.js', () => ({ default: () => api.available }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: null, isError: false }) }));
vi.mock('../hooks/useReviewCoolLoop.js', () => ({ useReviewCoolLoop: () => ({ autoReview: {}, coolUndecided: 0 }) }));
vi.mock('../components/library/ReadNextView.jsx', () => ({ default: ({ items }) => <div>{items.map(row => <p key={row.item_key}>{row.title}</p>)}</div> }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('shows the admitted working set before an unavailable-Mac network check finishes', async () => {
  api.available = false;
  await applyPull({ cursor: 4, papers: [{ item_key: 'COLD', title: 'Cached before network', revisions: {} }] });
  let finish;
  const reply = new Promise((resolve) => { finish = resolve; });
  api.fetchReadingQueue.mockReturnValue(reply);
  render(<StrictMode><MemoryRouter><LibraryReadNext /></MemoryRouter></StrictMode>);
  try {
    expect(await screen.findByText('Cached before network', { exact: true })).toBeTruthy();
  } finally {
    await act(async () => { finish({ status: 'offline', offline: true, items: [{ item_key: 'COLD', title: 'Cached before network' }] }); });
  }
});

it('does not restart an earlier queue scope after its in-flight status poll settles', async () => {
  vi.useFakeTimers();
  api.available = true;
  api.fetchReadingQueue.mockClear();
  api.fetchReadingQueueStatus.mockReset();
  let finish;
  const status = new Promise((resolve) => { finish = resolve; });
  api.fetchReadingQueueStatus.mockReturnValue(status);
  api.fetchReadingQueue.mockImplementation(async ({ search }) => ({
    status: search ? 'ready' : 'computing', model_ready: true,
    items: [{ item_key: search ? 'B' : 'A', title: search ? 'Scope B' : 'Scope A' }],
  }));
  await act(async () => { render(<MemoryRouter><LibraryReadNext /></MemoryRouter>); });
  try {
    expect(screen.getByText('Scope A', { exact: true })).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(api.fetchReadingQueueStatus).toHaveBeenCalledOnce();
    const input = screen.getByPlaceholderText('Search by meaning… (e.g. hallucination mitigation)', { exact: true });
    fireEvent.change(input, { target: { value: 'B' } });
    await act(async () => { fireEvent.keyUp(input, { key: 'Enter' }); });
    expect(screen.getByText('Scope B', { exact: true })).toBeTruthy();
  } finally {
    await act(async () => { finish({ running: false }); });
  }
  expect(api.fetchReadingQueue).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Scope B', { exact: true })).toBeTruthy();
});
