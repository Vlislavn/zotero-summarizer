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
const key = (target, name, init = {}) => {
  const event = new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init });
  fireEvent(target, event);
  return event;
};
const search = () => screen.getByPlaceholderText(/Search by meaning/);
const heading = () => within(screen.queryByRole('dialog') || screen.getByTestId('library-detail')).getAllByRole('heading', { level: 2 })[0].textContent;
const selected = () => document.querySelector('[aria-current="true"]')?.dataset.rowKey;
const verdicts = () => golden.submitVerdict.mock.calls.map(([body]) => `${body.item_key}:${body.user_priority}`);

function setViewport(desktop) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: desktop, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(),
  }));
}

async function mount({ desktop = false, entry = '/library' } = {}) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  setViewport(desktop);
  const items = Array.from({ length: 6 }, (_, i) => ({ item_key: `K${i + 1}`, title: `Paper ${i + 1}`, relevance_score: 4 }));
  api.fetchReadingQueue.mockResolvedValue({ items, status: 'ready', model_ready: true });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={[entry]}><LibraryReadNext /></MemoryRouter></QueryClientProvider>);
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
  Element.prototype.scrollIntoView = vi.fn();
  useReviewCoolLoop.mockReturnValue({ autoReview: {}, coolUndecided: 0 });
  api.fetchDeepReviewStatus.mockResolvedValue({ status: 'idle' });
  settings.fetchLlmReachability.mockResolvedValue({ stages: [] });
  golden.submitVerdict.mockResolvedValue({ verdict: {} });
  golden.deleteVerdict.mockResolvedValue({ deleted: true });
  golden.fetchReviewDetail.mockImplementation(async (k) => ({
    item_key: k, title: `Paper ${k.slice(1)}`, abstract: 'An abstract.', deep_review: null,
    tags: [], collections: [], user_note: '', questions: [], verdict: null,
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('after a click on a row, j follows to the next paper and 2 sets its verdict', async () => {
  await mount();
  fireEvent.click(screen.getByText('Paper 2'));
  await flush();
  expect(heading()).toBe('Paper 2');

  key(document.activeElement, 'j');
  expect(selected()).toBe('K3');
  await flush(250);
  expect(heading()).toBe('Paper 3');
  await flush(QUIET_MS);
  key(document.activeElement, '2');
  await flush();

  expect(verdicts()).toEqual(['K3:should_read']);
  expect(heading()).toBe('Paper 4');
});

it('with the focus left on the clicked row button (wide screen) j and 2 still work, and the hint line shows', async () => {
  await mount({ desktop: true });
  expect(screen.getByTestId('key-hint').textContent).toMatch(/1–4 verdict/);
  fireEvent.click(screen.getByText('Paper 1'));
  await flush();
  const row = document.querySelector('[data-row-key="K1"] button');
  row.focus();

  key(row, 'j');
  expect(selected()).toBe('K2');
  key(row, '2');  // the paper has not followed yet: this must not hit K1
  await flush(250);
  await flush(QUIET_MS);
  expect(golden.submitVerdict).not.toHaveBeenCalled();
  key(row, '2');
  await flush();
  expect(verdicts()).toEqual(['K2:should_read']);
});

it('a typed 4 in a note is a character, not a verdict', async () => {
  await mount({ entry: '/library?open=K1' });
  await flush(QUIET_MS);
  const note = screen.getByRole('textbox', { name: 'Manual notes', exact: true });

  expect(key(note, '4').defaultPrevented).toBe(false);
  expect(golden.submitVerdict).not.toHaveBeenCalled();
});

it('a typed 4 (or j) in the search box is a character', async () => {
  await mount();

  expect(key(search(), '4').defaultPrevented).toBe(false);
  expect(key(search(), 'j').defaultPrevented).toBe(false);
  expect(golden.submitVerdict).not.toHaveBeenCalled();
});

it('holding 4 gives one verdict', async () => {
  await mount({ entry: '/library?open=K1' });
  await flush(QUIET_MS);

  key(document.body, '4');
  for (let i = 0; i < 4; i += 1) key(document.body, '4', { repeat: true });
  await flush();

  expect(verdicts()).toEqual(['K1:dont_read']);
});

it('Esc closes the detail and leaves what is typed in the search box; / puts the cursor there', async () => {
  await mount({ desktop: true, entry: '/library?open=K1' });
  fireEvent.change(search(), { target: { value: 'graph' } });
  expect(screen.getByTestId('library-detail')).toBeTruthy();

  key(document.body, 'Escape');
  await flush();
  expect(screen.queryByTestId('library-detail')).toBeNull();
  expect(search().value).toBe('graph');

  expect(key(document.body, '/').defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(search());
});

it('z takes the last verdict back while its toast shows', async () => {
  await mount({ entry: '/library?open=K1' });
  await flush(QUIET_MS);
  key(document.body, '1');
  await flush();
  expect(within(screen.getByTestId('undo-toast')).getByText(/Must read/)).toBeTruthy();

  key(document.body, 'z');
  await flush();
  expect(golden.deleteVerdict).toHaveBeenCalledWith('K1');
  expect(heading()).toBe('Paper 1');
});
