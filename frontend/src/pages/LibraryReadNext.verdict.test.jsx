// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import * as golden from '../api/goldenApi.js';
import * as api from '../api/libraryApi.js';
import * as settings from '../api/settingsApi.js';
import { QUIET_MS } from '../components/library/VerdictBar.jsx';
import LibraryReadNext from './LibraryReadNext.jsx';
import { useReviewCoolLoop } from '../hooks/useReviewCoolLoop.js';

vi.mock('../api/goldenApi.js');
vi.mock('../api/libraryApi.js');
vi.mock('../api/settingsApi.js');
vi.mock('../hooks/useServerAvailable.js', () => ({ default: () => true }));
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: () => ({ status: { zotero: { db_found: true } } }) }));
vi.mock('../hooks/useReviewCoolLoop.js', () => ({ useReviewCoolLoop: vi.fn() }));
vi.mock('../offlineStore.js', async (original) => ({ ...(await original()), allPapers: async () => [] }));

const flush = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const dialog = () => screen.getByRole('dialog');
const bar = () => within(screen.getByTestId('verdict-bar'));
const heading = () => within(dialog()).getByRole('heading', { level: 2 }).textContent;
const press = async (name) => { await flush(QUIET_MS); fireEvent.click(bar().getByRole('button', { name })); await flush(); };
const todo = (n) => screen.getByText(`To decide · ${n}`);

async function mount(count = 12) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const items = Array.from({ length: count }, (_, i) => ({ item_key: `K${i + 1}`, title: `Paper ${i + 1}`, relevance_score: 4 }));
  api.fetchReadingQueue.mockResolvedValue({ items, status: 'ready', model_ready: true });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={['/library?open=K1']}><LibraryReadNext /></MemoryRouter></QueryClientProvider>);
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
  useReviewCoolLoop.mockReturnValue({ autoReview: {}, coolUndecided: 0 });
  api.fetchDeepReviewStatus.mockResolvedValue({ status: 'idle' });
  settings.fetchLlmReachability.mockResolvedValue({ stages: [] });
  golden.submitVerdict.mockResolvedValue({ verdict: {} });
  golden.deleteVerdict.mockResolvedValue({ deleted: true });
  golden.fetchReviewDetail.mockImplementation(async (key) => ({
    item_key: key, title: `Paper ${key.slice(1)}`, abstract: 'An abstract.', deep_review: null,
    tags: [], collections: [], user_note: '', questions: [], verdict: null,
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('a pill takes the paper out of «To decide» without re-reading the queue, opens the next undecided one, and ignores taps for 500 ms', async () => {
  await mount();
  await press('Should read');
  expect(golden.submitVerdict).toHaveBeenCalledWith(expect.objectContaining({ item_key: 'K1', user_priority: 'should_read', surface: 'library_sheet' }));
  expect(todo(11)).toBeTruthy();
  expect(heading()).toBe('Paper 2');
  expect(api.fetchReadingQueue).toHaveBeenCalledTimes(1);
  fireEvent.click(bar().getByRole('button', { name: 'Must read' }));  // the same pill, under the same finger: ignored
  await flush();
  expect(golden.submitVerdict).toHaveBeenCalledTimes(1);
  await press('Must read');
  expect(golden.submitVerdict).toHaveBeenCalledTimes(2);
});

it('10 verdicts in a row ask for the queue 0 times; closing the detail reconciles with one read', async () => {
  await mount();
  for (let i = 0; i < 10; i += 1) await press('Could read');
  expect(golden.submitVerdict).toHaveBeenCalledTimes(10);
  expect(api.fetchReadingQueue).toHaveBeenCalledTimes(1);  // the first load only
  fireEvent.click(within(dialog()).getAllByRole('button', { name: 'Close' })[0]);
  await flush();
  expect(api.fetchReadingQueue).toHaveBeenCalledTimes(2);
});

it('Undo takes the verdict back and returns to the paper and its row', async () => {
  await mount();
  await press('Should read');
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Undo' }));
  await flush();
  expect(golden.deleteVerdict).toHaveBeenCalledWith('K1');
  expect(heading()).toBe('Paper 1');
  expect(todo(12)).toBeTruthy();
});

it('says «All caught up in this list» after the last undecided paper', async () => {
  await mount(1);
  await press('Must read');
  expect(heading()).toBe('All caught up in this list');
  expect(within(dialog()).getByRole('button', { name: 'Back to list' })).toBeTruthy();
});

it('puts the row back and offers Retry when the save fails', async () => {
  await mount(3);
  golden.submitVerdict.mockRejectedValueOnce(new Error('boom'));
  await press('Could read');
  expect(todo(3)).toBeTruthy();
  expect(within(dialog()).getByText('Couldn’t save “Paper 1”')).toBeTruthy();
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Retry' }));
  await flush();
  expect(golden.submitVerdict).toHaveBeenCalledTimes(2);
});

it('hides the verdict bar while a text field in the detail has focus', async () => {
  await mount();
  const note = within(dialog()).getByRole('textbox', { name: 'Manual notes', exact: true });
  act(() => note.focus());
  expect(screen.getByTestId('verdict-bar').className).toMatch(/\bhidden\b/);
});

it('a batch of 3 in Select mode gives one toast and one Undo for all three', async () => {
  await mount(5);
  fireEvent.click(within(dialog()).getAllByRole('button', { name: 'Close' })[0]);
  fireEvent.click(screen.getByRole('button', { name: 'Select' }));
  ['Paper 1', 'Paper 2', 'Paper 3'].forEach((title) => fireEvent.click(screen.getByRole('checkbox', { name: `Select ${title}` })));
  fireEvent.click(screen.getByRole('button', { name: 'Set Could read for 3 selected' }));
  await flush();
  expect(golden.submitVerdict.mock.calls.map(([body]) => body.surface)).toEqual(Array(3).fill('library_bulk'));
  expect(screen.getAllByTestId('undo-toast')).toHaveLength(1);
  expect(screen.getByText('Could read: 3 papers')).toBeTruthy();
  expect(todo(2)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  await flush();
  expect(golden.deleteVerdict.mock.calls.map(([key]) => key)).toEqual(['K1', 'K2', 'K3']);
  expect(todo(5)).toBeTruthy();
  expect(api.fetchReadingQueue).toHaveBeenCalledTimes(1);
});
