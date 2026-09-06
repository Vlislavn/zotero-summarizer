// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import * as api from '../api/libraryApi.js';
import LibraryReadNext from './LibraryReadNext.jsx';

vi.mock('../api/libraryApi.js');
vi.mock('../hooks/useSetupStatus.js', () => ({
  useSetupStatus: () => ({ status: { zotero: { db_found: true } } }),
}));
vi.mock('../hooks/useReviewCoolLoop.js', () => ({
  useReviewCoolLoop: () => ({ autoReview: {}, coolUndecided: 0 }),
}));
vi.mock('../components/library/ReadNextView.jsx', () => ({ default: () => null }));

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
  api.fetchCollections.mockResolvedValue({ items: [] });
  api.fetchTags.mockResolvedValue({ items: [] });
  api.fetchReadingQueue.mockResolvedValue({ items: [] });
  api.fetchFulltext.mockResolvedValue({ status: 'started' });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function startFetch() {
  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  fireEvent.click(screen.getByText('Zotero'));
  fireEvent.click(screen.getByRole('button', { name: 'Fetch full text' }));
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

  startFetch();

  expect(await screen.findByText(
    `Attached ${attached} full-text PDF(s) to Zotero (skipped ${skipped} already attached; ${unavailable} unavailable).`
    + (attached ? ' They upload to zotero.org on the next sync.' : '')
    + ' Backup: /backup/zotero.sqlite.',
  )).toBeTruthy();
  expect(api.fetchFulltext).toHaveBeenCalledWith({ force: false });
  expect(api.fetchFulltextStatus).toHaveBeenCalledTimes(1);
});

it.each([
  [{ running: false, result: null }, 'Full-text result is missing outcomes'],
  [{ running: false, result: { error: 'resolver failed' } }, 'resolver failed'],
])('does not report an absent or failed result as success', async (status, message) => {
  api.fetchFulltextStatus.mockResolvedValue(status);
  startFetch();
  expect(await screen.findByText(`Full-text fetch failed: ${message}`)).toBeTruthy();
  expect(screen.queryByText(/Attached 0/)).toBeNull();
});

it('surfaces a status request failure instead of silently retrying', async () => {
  vi.useFakeTimers();
  api.fetchFulltextStatus.mockRejectedValue(new Error('status endpoint unavailable'));
  startFetch();
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
  expect(screen.getByText(/Full-text status unavailable: status endpoint unavailable/)).toBeTruthy();
  expect(api.fetchFulltextStatus).toHaveBeenCalledTimes(1);
});
