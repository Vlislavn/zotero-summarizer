// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as golden from '../../api/goldenApi.js';
import * as library from '../../api/libraryApi.js';
import * as settings from '../../api/settingsApi.js';
import ReadNextView from './ReadNextView.testkit.jsx';

vi.mock('../../api/goldenApi.js');
vi.mock('../../api/libraryApi.js');
vi.mock('../../api/settingsApi.js');
vi.mock('../../hooks/useServerAvailable.js', () => ({ default: () => true }));

const rows = [
  { item_key: 'K1', title: 'Plain paper', relevance_score: 3 },
  { item_key: 'K2', title: 'Reviewed paper', relevance_score: 4, review_digest: { tldr: 'It works.' } },
];
const details = {
  K1: { item_key: 'K1', title: 'Plain paper', abstract: 'The plain abstract text.', deep_review: null, tags: [], collections: [], user_note: '', questions: [], verdict: null },
  K2: { item_key: 'K2', title: 'Reviewed paper', abstract: 'The reviewed abstract text.', deep_review: { digest: { tldr: 'It works.' } }, tags: [], collections: [], user_note: '', questions: [], verdict: null },
};
const clients = [];
beforeEach(() => {
  golden.fetchReviewDetail.mockImplementation(async (key) => details[key]);
  library.fetchDeepReviewStatus.mockResolvedValue({ status: 'idle' });
  library.fetchTags.mockResolvedValue({ items: [] });
  settings.fetchLlmReachability.mockResolvedValue({ stages: [] });
});
afterEach(() => { cleanup(); clients.splice(0).forEach((c) => c.clear()); vi.resetAllMocks(); });

function mount(entry = '/library?open=K1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><ReadNextView
    items={rows} totalUnread={2} rawCount={2} status="ready" modelReady onSaved={vi.fn()} /></MemoryRouter></QueryClientProvider>);
}
const dialog = () => screen.getByRole('dialog');

it('asks inside the detail before an exit that would lose an unsaved note, whichever exit it is', async () => {
  mount();
  const note = await within(dialog()).findByRole('textbox', { name: 'Manual notes', exact: true });
  fireEvent.change(note, { target: { value: 'half a thought' } });

  fireEvent.click(within(dialog()).getByRole('button', { name: 'Next paper' }));
  expect(within(dialog()).getByRole('alert').textContent).toMatch(/unsaved notes/);
  expect(screen.getByTestId('location').textContent).toBe('/library?open=K1');
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Keep editing' }));
  expect(within(dialog()).queryByRole('alert')).toBeNull();
  expect(within(dialog()).getByRole('textbox', { name: 'Manual notes', exact: true }).value).toBe('half a thought');

  fireEvent.keyDown(document, { key: 'Escape' });  // Esc is an exit too
  expect(within(dialog()).getByRole('alert')).toBeTruthy();
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Close' }));
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Leave' }));

  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByTestId('location').textContent).toBe('/library');
});

it('opening a detail only reads: one GET review-detail, no verdict, no build, no POST', async () => {
  mount('/library');

  fireEvent.click(screen.getByText('Plain paper', { selector: 'span' }));
  await within(dialog()).findByRole('textbox', { name: 'Manual notes', exact: true });  // the loaded detail

  expect(golden.fetchReviewDetail).toHaveBeenCalledTimes(1);
  expect(golden.fetchReviewDetail).toHaveBeenCalledWith('K1');
  expect(golden.submitVerdict).not.toHaveBeenCalled();
  expect(golden.deleteVerdict).not.toHaveBeenCalled();
  const called = Object.entries(library).filter(([, fn]) => vi.isMockFunction(fn) && fn.mock.calls.length).map(([name]) => name);
  expect(called.filter((name) => !name.startsWith('fetch'))).toEqual([]);  // every API call the detail made is a read
  expect(called).not.toContain('fetchPaperRender');
});
