// @vitest-environment jsdom
// Decided cards leave the slate (#206): one toast per decision, Undo restores the card, nothing decided
// comes back on reload, opening Today writes nothing, a parked Add shows only in the banner.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const daily = vi.hoisted(() => ({
  fetchDailySlate: vi.fn(), fetchDailyMembers: vi.fn(), addToLibrary: vi.fn(), trashPapers: vi.fn(),
  triggerTriageBacklog: vi.fn(), getTriageStatus: vi.fn(), submitDailyVerdict: vi.fn(), undoDailyVerdict: vi.fn(),
}));
vi.mock('../api/dailyApi.js', () => daily);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: vi.fn(() => ({ status: { ready: true }, isConfigured: true })) }));
vi.mock('../components/CollectionPicker.jsx', () => ({ default: () => null }));

import Today from './Today.jsx';

const papers = [1, 2].map(id => ({ item_id: id, item_key: `P${id}`, stable_feed_key: `feed:P${id}`, title: `Paper ${id}` }));
const open = paper => ({ item_id: paper.item_id, item_key: paper.item_key, stable_feed_key: paper.stable_feed_key,
  available: true, decision: 'triaged_pending', review_ready: true, user_priority: null, zotero_sync_status: null });
let facts;

beforeEach(() => {
  const stored = new Map();
  vi.stubGlobal('localStorage', {
    getItem: key => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
    removeItem: key => stored.delete(key),
  });
  facts = new Map(papers.map(paper => [paper.item_id, open(paper)]));
  daily.fetchDailySlate.mockResolvedValue({ papers, awaiting_review_total: 2 });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  daily.fetchDailyMembers.mockImplementation(async ids => ({ members: ids.map(id => facts.get(id)) }));
  daily.submitDailyVerdict.mockImplementation(async ({ itemId, priority }) => {
    facts.set(itemId, { ...facts.get(itemId), user_priority: priority });
    return { id: itemId };
  });
  daily.undoDailyVerdict.mockImplementation(async itemId => {
    facts.set(itemId, open(papers[itemId - 1]));
    return { deleted: true };
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

const mount = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);
  return client;
};
const cardOf = async title => (await screen.findByText(title)).closest('article');
// The buttons enable once the member status has loaded; click only then.
const pick = async (title, name) => {
  const card = await cardOf(title);
  await waitFor(() => expect(within(card).getByRole('button', { name }).disabled).toBe(false));
  fireEvent.click(within(card).getByRole('button', { name }));
};
const titles = () => screen.queryAllByRole('article').map(card => within(card).getByRole('link').textContent);

it('a decision removes the card at once and shows ONE toast with Undo; the card carries no reason chips', async () => {
  const client = mount();
  await pick('Paper 1', 'must');
  await waitFor(() => expect(titles()).toEqual(['Paper 2']));
  expect(daily.submitDailyVerdict).toHaveBeenCalledWith({ itemId: 1, priority: 'must_read', chips: [] });
  expect(screen.getAllByTestId('undo-toast')).toHaveLength(1);
  expect(screen.getByTestId('undo-toast').textContent).toContain('Must read: “Paper 1”');
  expect(screen.queryByText(/Reason \(optional/)).toBeNull();
  // a second decision replaces the toast: still one
  await pick('Paper 2', 'Not interested');
  await screen.findByText('All caught up');
  expect(screen.getAllByTestId('undo-toast')).toHaveLength(1);
  expect(screen.getByTestId('undo-toast').textContent).toContain('Not interested: “Paper 2”');
  client.clear();
});

it('reasons are offered inside the toast, matching the decision, and go out as the same verdict', async () => {
  const client = mount();
  await pick('Paper 1', 'must');
  fireEvent.click(await screen.findByRole('button', { name: 'worth taking' }));
  await waitFor(() => expect(daily.submitDailyVerdict).toHaveBeenLastCalledWith({ itemId: 1, priority: 'must_read', chips: ['has_takeaway'] }));
  expect(screen.getByRole('button', { name: 'worth taking' }).getAttribute('aria-pressed')).toBe('true');
  client.clear();
});

it('Undo puts the card back in its place, undecided', async () => {
  const client = mount();
  await pick('Paper 1', 'should');
  await waitFor(() => expect(titles()).toEqual(['Paper 2']));
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  await waitFor(() => expect(daily.undoDailyVerdict).toHaveBeenCalledWith(1));
  await waitFor(() => expect(titles()).toEqual(['Paper 1', 'Paper 2']));
  const card = screen.getAllByRole('article')[0];
  for (const name of ['must', 'should', 'could', 'Not interested']) {
    expect(within(card).getByRole('button', { name }).disabled).toBe(false);
  }
  expect(screen.queryByTestId('undo-toast')).toBeNull();
  client.clear();
});

it('a reload shows no decided cards, and "All caught up" when every paper is decided', async () => {
  facts.set(1, { ...facts.get(1), user_priority: 'must_read' });
  const first = mount();
  await waitFor(() => expect(titles()).toEqual(['Paper 2']));
  await pick('Paper 2', 'Not interested');
  await screen.findByText('All caught up');
  first.clear(); cleanup();
  // reload: a new client reads the stored visit again; the slate is not re-selected
  const second = mount();
  await screen.findByText('All caught up');
  expect(screen.queryByRole('article')).toBeNull();
  expect(daily.fetchDailySlate).toHaveBeenCalledTimes(1);
  second.clear();
});

it('opening Today performs no write', async () => {
  const client = mount();
  await cardOf('Paper 1');
  await waitFor(() => expect(daily.fetchDailyMembers).toHaveBeenCalled());
  for (const write of [daily.submitDailyVerdict, daily.undoDailyVerdict, daily.addToLibrary, daily.trashPapers, daily.triggerTriageBacklog]) {
    expect(write).not.toHaveBeenCalled();
  }
  client.clear();
});

it('a parked Add is visible only through the banner, never as a card', async () => {
  facts.set(1, { ...facts.get(1), decision: 'user_approved', zotero_sync_status: 'pending', user_priority: 'must_read' });
  const client = mount();
  await screen.findByText(/1 paper waits for Zotero/);
  await waitFor(() => expect(titles()).toEqual(['Paper 2']));
  expect(screen.queryByText('Paper 1')).toBeNull();
  client.clear();
});

it('a positive pill whose Add parked says so in the toast, and the banner follows the refreshed facts', async () => {
  daily.submitDailyVerdict.mockImplementation(async ({ itemId, priority }) => {
    facts.set(itemId, { ...facts.get(itemId), user_priority: priority, decision: 'user_approved', zotero_sync_status: 'pending' });
    return { id: itemId, add_attempted: true, add_status: 'zotero_pending', added_to_library: false, add_error: null };
  });
  const client = mount();
  await pick('Paper 1', 'must');
  await waitFor(() => expect(screen.getByTestId('undo-toast').textContent).toContain('Not in Zotero yet'));
  await screen.findByText(/1 paper waits for Zotero/);
  client.clear();
});

it('Undo of a decision whose Add already reached Zotero says the item stays there', async () => {
  daily.undoDailyVerdict.mockImplementation(async itemId => {
    facts.set(itemId, open(papers[itemId - 1]));
    return { deleted: true, released_pending_add: 0, item_stays_in_zotero: true, zotero_key: 'ZK1' };
  });
  const client = mount();
  await pick('Paper 1', 'should');
  await waitFor(() => expect(titles()).toEqual(['Paper 2']));
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
  await waitFor(() => expect(screen.getByTestId('undo-toast').textContent).toContain('“Paper 1” stays in Zotero'));
  expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  client.clear();
});
