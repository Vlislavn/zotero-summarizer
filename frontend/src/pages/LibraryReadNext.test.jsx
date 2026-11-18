// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import * as api from '../api/libraryApi.js';
import LibraryReadNext from './LibraryReadNext.jsx';
import { useReviewCoolLoop } from '../hooks/useReviewCoolLoop.js';

vi.mock('../api/libraryApi.js');
vi.mock('../hooks/useServerAvailable.js', () => ({ default: () => true }));
vi.mock('../hooks/useSetupStatus.js', () => ({
  useSetupStatus: () => ({ status: { zotero: { db_found: true } } }),
}));
vi.mock('../hooks/useReviewCoolLoop.js', () => ({ useReviewCoolLoop: vi.fn() }));
vi.mock('../components/library/ReadNextView.jsx', () => ({ default: () => null }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
  useReviewCoolLoop.mockReturnValue({ autoReview: {}, coolUndecided: 0 });
  api.fetchCollections.mockResolvedValue({ items: [] });
  api.fetchTags.mockResolvedValue({ items: [] });
  api.fetchReadingQueue.mockResolvedValue({ items: [] });
  api.fetchFulltext.mockResolvedValue({ status: 'started' });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const flush = () => act(async () => {});
const status = () => screen.getByTestId('library-status');
// The result of a job is the status line's text (not a banner at the page bottom).
const inStatusLine = async (text) => (await screen.findByText(text)).closest('[data-testid="library-status"]');

async function startFetch() {
  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  await flush();
  api.fetchFulltextStatus.mockClear();  // the on-open probe is not the fetch under test
  fireEvent.click(screen.getByText('Library jobs'));
  fireEvent.click(screen.getByRole('button', { name: 'Fetch missing PDFs…' }));
}

it.each([
  [['offline_uncached', 'browser_extra_unavailable'], 0, 0, 2],
  [['attached_arxiv', 'attached_cached', 'skipped_has_pdf', 'no_oa_source', 'needs_login',
    'fetch_failed', 'write_failed', 'browser_not_attempted', 'offline_uncached',
    'browser_extra_unavailable'], 2, 1, 7],
  [['skipped_has_pdf'], 0, 1, 0],
  [[], 0, 0, 0],
])('reports every unavailable outcome: %j', async (statuses, attached, skipped, unavailable) => {
  api.fetchFulltextStatus.mockResolvedValue({ running: false, result: {
    attached, skipped_has_pdf: skipped, backup_path: '/backup/zotero.sqlite',
    outcomes: statuses.map((status, i) => ({ item_key: String(i), status })),
  } });

  await startFetch();

  expect(await inStatusLine(
    `Attached ${attached} full-text PDF(s) to Zotero (skipped ${skipped} already attached; ${unavailable} unavailable).`
    + (attached ? ' They upload to zotero.org on the next sync.' : '')
    + ' Backup: /backup/zotero.sqlite.',
  )).not.toBeNull();
  expect(api.fetchFulltext).toHaveBeenCalledWith({ force: false });
  expect(api.fetchFulltextStatus).toHaveBeenCalledTimes(1);
});

it.each([
  [{ running: false, result: null }, 'Full-text result is missing outcomes'],
  [{ running: false, result: { error: 'resolver failed' } }, 'resolver failed'],
])('does not report an absent or failed result as success', async (state, message) => {
  api.fetchFulltextStatus.mockResolvedValue(state);
  await startFetch();
  expect(await inStatusLine(`Full-text fetch failed: ${message}`)).not.toBeNull();
  expect(screen.queryByText(/Attached 0/)).toBeNull();
});

it('surfaces a status request failure instead of silently retrying', async () => {
  vi.useFakeTimers();
  api.fetchFulltextStatus.mockRejectedValue(new Error('status endpoint unavailable'));
  await startFetch();
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
  expect(screen.getByText(/Full-text status unavailable: status endpoint unavailable/)).toBeTruthy();
  expect(api.fetchFulltextStatus).toHaveBeenCalledTimes(1);
});

it('reflects a running review fleet on open: one status GET, no run POST', async () => {
  const actual = await vi.importActual('../hooks/useReviewCoolLoop.js');
  useReviewCoolLoop.mockImplementation(actual.useReviewCoolLoop);
  api.fetchReviewFleetStatus.mockResolvedValue({ status: 'running', total: 5, completed: 1 });

  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);

  expect(await screen.findByText(/Reviewing paper 2 of 5/)).toBeTruthy();
  expect(api.fetchReviewFleetStatus).toHaveBeenCalledTimes(1);
  expect(api.runReviewFleet).not.toHaveBeenCalled();
});

it('resumes a running full-text fetch on open as the status line, without starting another', async () => {
  api.fetchFulltextStatus.mockResolvedValue({ running: true, progress: { done: 1, total: 4 } });

  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  await flush();

  expect(status().textContent).toMatch(/Fetching full text… 1\/4 checked/);
  expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();  // progress, not a result to dismiss
  fireEvent.click(screen.getByText('Library jobs'));
  expect(screen.getByRole('button', { name: 'Fetching…' }).disabled).toBe(true);
  expect(api.fetchFulltext).not.toHaveBeenCalled();
});

it('does not show a finished old full-text result on open', async () => {
  api.fetchFulltextStatus.mockResolvedValue({ running: false, result: { attached: 3, outcomes: [] } });

  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  await flush();

  expect(screen.queryByText(/Attached 3/)).toBeNull();
  expect(api.fetchFulltextStatus).toHaveBeenCalledTimes(1);
});
