// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import * as api from '../api/libraryApi.js';
import LibraryReadNext from './LibraryReadNext.jsx';
import { useReviewCoolLoop } from '../hooks/useReviewCoolLoop.js';

vi.mock('../api/libraryApi.js');
vi.mock('../hooks/useServerAvailable.js', () => ({ default: () => true }));
vi.mock('../hooks/useSetupStatus.js', () => ({
  useSetupStatus: () => ({ status: { zotero: { db_found: true } } }),
}));
vi.mock('../hooks/useReviewCoolLoop.js', () => ({ useReviewCoolLoop: vi.fn() }));
vi.mock('../components/library/ReadNextView.jsx', () => ({
  default: ({ items }) => <ul>{items.map((it) => <li key={it.item_key}>{it.title}</li>)}</ul>,
}));

// Two must/should rows and two could rows; the Why reason is on rows of both bands.
const QUEUE = [
  { item_key: 'A', title: 'Alpha', relevance_score: 4.8, why_reason: 'x' },
  { item_key: 'B', title: 'Bravo', relevance_score: 2.5, why_reason: 'x' },
  { item_key: 'C', title: 'Charlie', relevance_score: 2.7, why_reason: 'y' },
  { item_key: 'D', title: 'Delta', relevance_score: 3.6, why_reason: 'x' },
];

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
  useReviewCoolLoop.mockReturnValue({ autoReview: {}, coolUndecided: 0 });
  api.fetchCollections.mockResolvedValue({ items: [] });
  api.fetchTags.mockResolvedValue({ items: [] });
  api.fetchReadingQueue.mockResolvedValue({ items: QUEUE });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Query() {
  return <output data-testid="query">{useLocation().search}</output>;
}

function open(url) {
  render(<MemoryRouter initialEntries={[url]}><Query /><LibraryReadNext /></MemoryRouter>);
}

it('an old link with w= is filtered only by b, and w is dropped from the URL', async () => {
  open('/library?w=x&b=could_read');

  expect(await screen.findByText('Bravo')).toBeTruthy();
  expect(screen.getByText('Charlie')).toBeTruthy();   // reason «y»: a w filter would hide it
  expect(screen.queryByText('Alpha')).toBeNull();
  expect(screen.queryByText('Delta')).toBeNull();
  await waitFor(() => expect(screen.getByTestId('query').textContent).toBe('?b=could_read'));
});

it('has one Clear all, and it resets the scope and the smart filters together', async () => {
  open('/library?b=could_read');
  await screen.findByText('Bravo');

  const clears = screen.getAllByRole('button', { name: /clear/i, hidden: true });
  expect(clears.map((b) => b.textContent)).toEqual(['Clear all']);
  fireEvent.click(clears[0]);

  expect(await screen.findByText('Alpha')).toBeTruthy();
  expect(screen.getByText('Delta')).toBeTruthy();
  await waitFor(() => expect(screen.getByTestId('query').textContent).toBe(''));
  expect(screen.queryByRole('button', { name: /clear/i, hidden: true })).toBeNull();
});

it('Model guess chips carry their counts and filter the list', async () => {
  open('/library');
  await screen.findByText('Alpha');

  for (const label of ['must 1', 'should 1', 'could 2']) {
    expect(screen.getByText(label)).toBeTruthy();
  }
  expect(screen.queryByText(/Not interested/)).toBeNull();   // no rows, so no dead chip
  expect(screen.queryByText(/score band/i)).toBeNull();
  fireEvent.click(screen.getByText('could 2'));
  await waitFor(() => expect(screen.queryByText('Alpha')).toBeNull());
  expect(screen.getByText('Bravo')).toBeTruthy();
  expect(screen.getByTestId('query').textContent).toBe('?b=could_read');
});
