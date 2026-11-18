// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
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
const inStatusLine = async (text) => (await screen.findByText(text, { exact: typeof text === 'string' })).closest('[data-testid="library-status"]');

async function startFetch() {
  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  await flush();
  api.fetchFulltextStatus.mockClear();  // the on-open probe is not the fetch under test
  fireEvent.click(screen.getByText('Library jobs'));
  fireEvent.click(screen.getByRole('button', { name: 'Fetch missing PDFs…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Fetch PDFs' }));
}

// Zotero writes always ask first, in a sheet on the page (never window.confirm).
const sheet = () => screen.getByRole('dialog');
async function openSend() {
  api.fetchReadingQueue.mockResolvedValue({ items: [], computed_at: '2026-11-01T00:00:00Z', total_unread: 12, read_hidden: 3 });
  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  await flush();
  api.fetchReadingQueue.mockClear();
  fireEvent.click(screen.getByText('Library jobs'));
  fireEvent.click(screen.getByRole('button', { name: 'Send to Zotero…' }));
  return screen.getByRole('dialog');
}
const confirmSend = () => act(async () => { fireEvent.click(within(sheet()).getByRole('button', { name: 'Send to Zotero' })); });
const writes = () => [api.syncRelTags, api.syncScoreRanks, api.fetchFulltext, api.fetchReadingQueue];

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

it('Send to Zotero asks first with the paper count and the backup line; Cancel sends no request', async () => {
  const confirm = vi.spyOn(window, 'confirm');
  const dialog = await openSend();
  expect(within(dialog).getByRole('heading', { name: 'Send 15 papers to Zotero?' })).toBeTruthy();
  expect(dialog.textContent).toMatch(/Backup first: a copy of the Zotero database is saved/);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  await flush();
  expect(screen.queryByRole('dialog')).toBeNull();
  writes().forEach((write) => expect(write).not.toHaveBeenCalled());
  expect(confirm).not.toHaveBeenCalled();
});

it('Fetch missing PDFs asks first too; Cancel starts nothing', async () => {
  render(<MemoryRouter><LibraryReadNext /></MemoryRouter>);
  await flush();
  fireEvent.click(screen.getByText('Library jobs'));
  fireEvent.click(screen.getByRole('button', { name: 'Fetch missing PDFs…' }));
  expect(within(sheet()).getByRole('heading', { name: 'Fetch missing PDFs?' })).toBeTruthy();
  expect(sheet().textContent).toMatch(/Backup first/);
  fireEvent.click(within(sheet()).getByRole('button', { name: 'Cancel' }));
  await flush();
  expect(api.fetchFulltext).not.toHaveBeenCalled();
});

it('a running Zotero opens a second sheet; confirming repeats the write with force, cancelling stops', async () => {
  api.syncRelTags.mockResolvedValueOnce({ requires_force: true }).mockResolvedValue({ tagged: 1, failed_count: 0, backup_path: '/b/1.sqlite' });
  api.syncScoreRanks.mockResolvedValue({ ranked: 2, failed_count: 0, skipped_user_callnumber: 0, backup_path: '/b/2.sqlite' });
  await openSend();
  await confirmSend();
  expect(within(sheet()).getByRole('heading', { name: 'Zotero is running…' })).toBeTruthy();
  expect(api.syncScoreRanks).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(within(sheet()).getByRole('button', { name: 'Write anyway' })); });
  expect(api.syncRelTags.mock.calls.map(([arg]) => arg.force)).toEqual([false, true]);
  expect(await inStatusLine(/Zotero is up to date/)).not.toBeNull();

  api.syncRelTags.mockReset().mockResolvedValue({ requires_force: true });
  fireEvent.click(screen.getByRole('button', { name: 'Send to Zotero…' }));
  await confirmSend();
  fireEvent.click(within(sheet()).getByRole('button', { name: 'Cancel' }));
  await flush();
  expect(api.syncRelTags).toHaveBeenCalledTimes(1);
  expect(status().textContent).toMatch(/Sync cancelled/);
});

it('the receipt shows backup, failed and skipped, and is a warning when something failed', async () => {
  api.syncRelTags.mockResolvedValue({ tagged: 4, failed_count: 1, backup_path: '/b/tags.sqlite' });
  api.syncScoreRanks.mockResolvedValue({ ranked: 9, failed_count: 1, skipped_user_callnumber: 3, backup_path: '/b/ranks.sqlite' });
  await openSend();
  await confirmSend();
  const line = status();
  expect(line.textContent).toMatch(/^Warning:/);
  expect(line.className).toMatch(/amber/);
  expect(line.textContent).toMatch(/2 changes failed/);
  expect(line.textContent).toMatch(/3 papers kept their own Call Number/);
  expect(line.textContent).toMatch(/Backup: \/b\/tags\.sqlite, \/b\/ranks\.sqlite/);
});

it('a clean receipt is not a warning and still names the backup', async () => {
  api.syncRelTags.mockResolvedValue({ tagged: 4, failed_count: 0, backup_path: '/b/tags.sqlite' });
  api.syncScoreRanks.mockResolvedValue({ ranked: 9, failed_count: 0, skipped_user_callnumber: 0, backup_path: null });
  await openSend();
  await confirmSend();
  expect(status().textContent).not.toMatch(/Warning/);
  expect(status().textContent).toMatch(/Backup: \/b\/tags\.sqlite/);
});
