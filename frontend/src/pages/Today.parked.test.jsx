// @vitest-environment jsdom
// A parked Zotero Add on Today (#205): says what to do, stops polling, does not hold the page, Undo frees it.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const daily = vi.hoisted(() => ({
  fetchDailySlate: vi.fn(), fetchDailyMembers: vi.fn(), addToLibrary: vi.fn(), trashPapers: vi.fn(),
  triggerTriageBacklog: vi.fn(), getTriageStatus: vi.fn(), submitDailyVerdict: vi.fn(), undoDailyVerdict: vi.fn(),
}));
vi.mock('../api/dailyApi.js', () => daily);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: vi.fn(() => ({ status: { ready: true }, isConfigured: true })) }));
vi.mock('../components/CollectionPicker.jsx', () => ({ default: () => null }));

import Today from './Today.jsx';

const paper = { item_id: 32384, item_key: 'FEED32384', stable_feed_key: 'feed:g:abc', title: 'Parked paper' };
const parked = { item_id: 32384, item_key: 'FEED32384', stable_feed_key: 'feed:g:abc', available: true,
  decision: 'user_approved', zotero_sync_status: 'pending', user_priority: 'must_read', review_ready: true,
  note_delivery: { pending: false, target_key: null } };

beforeEach(() => {
  const stored = new Map();
  vi.stubGlobal('localStorage', {
    getItem: key => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
    removeItem: key => stored.delete(key),
  });
  daily.fetchDailySlate.mockResolvedValue({ papers: [paper], awaiting_review_total: 1 });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  daily.fetchDailyMembers.mockImplementation(async () => ({ members: [parked] }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

const mount = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Routes>
    <Route path="/" element={<Today />} /><Route path="/ops" element={<h1>Ops page</h1>} />
  </Routes></MemoryRouter></QueryClientProvider>);
  return client;
};

it('shows the owner what to do for a parked Add: one link to Ops › Feed review, in the banner, no card', async () => {
  const client = mount();
  const banner = await screen.findByText('1 paper waits for Zotero. Close Zotero, then apply them in Ops › Feed review.');
  expect(screen.queryByRole('article')).toBeNull();
  expect(screen.queryByText(/delivery pending/)).toBeNull();
  const link = within(banner).getByRole('link', { name: /Apply approved → Zotero/ });
  expect(link.getAttribute('href')).toBe('/ops?tab=review');
  fireEvent.click(link);
  expect(await screen.findByText('Ops page')).toBeTruthy();
  client.clear();
});

it('says papers wait for Zotero instead of "being added", and New shortlist stays available', async () => {
  const client = mount();
  await screen.findByText('1 paper waits for Zotero. Close Zotero, then apply them in Ops › Feed review.');
  expect(screen.queryByText(/still being added/)).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', { name: 'New shortlist' }).disabled).toBe(false));
  client.clear();
});

it('does not poll the members while the only unresolved paper is a parked Add', async () => {
  vi.useFakeTimers();
  const client = mount();
  try {
    for (let tick = 0; tick < 5; tick++) await act(() => vi.advanceTimersByTimeAsync(100)); // slate, then members, then facts
    expect(screen.getByText(/1 paper waits for Zotero/)).toBeTruthy();
    const calls = daily.fetchDailyMembers.mock.calls.length;
    for (let second = 0; second < 10; second++) await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(daily.fetchDailyMembers.mock.calls.length).toBe(calls);
  } finally {
    client.clear();
    vi.useRealTimers();
  }
});

it('Undo in the toast frees a decision that parked its Add: the card is back with four enabled buttons', async () => {
  let facts = { ...parked, decision: 'triaged_pending', zotero_sync_status: null, user_priority: null };
  daily.fetchDailyMembers.mockImplementation(async () => ({ members: [facts] }));
  daily.submitDailyVerdict.mockImplementation(async () => { facts = parked; return { id: 1 }; });
  daily.undoDailyVerdict.mockImplementation(async () => {
    facts = { ...parked, decision: 'triaged_pending', zotero_sync_status: null, user_priority: null };
    return { deleted: true, released_pending_add: 1 };
  });
  const client = mount();
  const card0 = await screen.findByRole('article');
  await waitFor(() => expect(within(card0).getByRole('button', { name: 'must' }).disabled).toBe(false));
  fireEvent.click(within(card0).getByRole('button', { name: 'must' }));
  await screen.findByText(/1 paper waits for Zotero/);
  expect(screen.queryByRole('article')).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
  await waitFor(() => expect(daily.undoDailyVerdict).toHaveBeenCalledWith(32384));
  const card = await screen.findByRole('article');
  await waitFor(() => {
    for (const name of ['must', 'should', 'could', 'Not interested']) {
      expect(within(card).getByRole('button', { name }).disabled).toBe(false);
    }
  });
  expect(screen.queryByText(/wait for Zotero|waits for Zotero/)).toBeNull();
  client.clear();
});
