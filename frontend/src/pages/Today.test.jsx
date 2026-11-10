// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const daily = vi.hoisted(() => ({
  fetchDailySlate: vi.fn(), fetchDailyMembers: vi.fn(), addToLibrary: vi.fn(), trashPapers: vi.fn(),
  triggerTriageBacklog: vi.fn(), getTriageStatus: vi.fn(),
}));
vi.mock('../api/dailyApi.js', () => daily);
vi.mock('../hooks/useSetupStatus.js', () => ({ useSetupStatus: vi.fn(() => ({ status: { ready: true }, isConfigured: true })) }));
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import NotConfiguredCard from '../components/setup/NotConfiguredCard.jsx';
vi.mock('../components/CollectionPicker.jsx', () => ({ default: () => null }));

import Today from './Today.jsx';

beforeEach(() => {
  const stored = new Map();
  vi.stubGlobal('localStorage', {
    getItem: key => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
    removeItem: key => stored.delete(key),
  });
  daily.fetchDailyMembers.mockReset();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

function memberFacts(paper, current = {}) {
  return { item_id: paper.item_id, item_key: paper.item_key, stable_feed_key: paper.stable_feed_key,
    available: true, decision: 'awaiting_review', review_ready: false, ...current };
}

it('keeps the shared setup nudge and route compatible with AI or ML-only choices', () => {
  useSetupStatus.mockReturnValueOnce({ status: { ready: false }, isConfigured: false, isLoading: false, isError: false });
  render(<MemoryRouter><Routes>
    <Route path="/" element={<NotConfiguredCard />} />
    <Route path="/setup" element={<h1>Setup destination</h1>} />
  </Routes></MemoryRouter>);
  expect(screen.getByText('Choose AI or ML-only operation and describe your research so the feed can be scored against your goals. Zotero can be connected later for sync.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Finish setup' }));
  expect(screen.getByRole('heading', { name: 'Setup destination' })).toBeTruthy();
});

it.each([
  ['Add to library', { added: 1, pending_sync: 1 },
    { decision: 'user_approved', zotero_sync_status: 'pending' }, /Decision saved; Zotero delivery pending/],
  ['Trash', { trashed: 1, marked_read: 0, marked_read_error: 'locked' },
    { decision: 'user_rejected', user_priority: 'dont_read' }, /Feed read marking failed: locked/],
])('retained-card %s keeps a visible warning when Zotero side effects fail', async (button, response, outcome, warning) => {
  const paper = { item_id: 5, item_key: 'P5', stable_feed_key: 'feed:P5', title: 'Retained paper' };
  let facts = memberFacts(paper, { review_ready: true });
  daily.fetchDailySlate.mockResolvedValue({ papers: [paper], awaiting_review_total: 1 });
  daily.fetchDailyMembers.mockImplementation(async () => ({ members: [facts] }));
  daily.getTriageStatus.mockResolvedValue({ running: false });
  const reply = async () => {
    facts = { ...facts, ...outcome };
    return { ...response, failed: [], failed_count: 0 };
  };
  daily.addToLibrary.mockImplementation(reply);
  daily.trashPapers.mockImplementation(reply);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText('Select Retained paper').disabled).toBe(false));
  fireEvent.click(screen.getByLabelText('Select Retained paper'));
  await waitFor(() => expect(screen.getByRole('button', { name: button }).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: button }));
  const card = screen.getByRole('article');
  const message = await within(card).findByText(warning);
  expect(message.getAttribute('role')).toBe('status');
  expect(within(card).getByText('Retained paper')).toBeTruthy();
  expect(screen.queryByText(/Spot-check/)).toBeNull();
  expect(daily.fetchDailySlate).toHaveBeenCalledTimes(1);
  if (button.startsWith('Add')) {
    expect(daily.addToLibrary).toHaveBeenCalledWith([5], '');
    expect(daily.trashPapers).not.toHaveBeenCalled();
  } else {
    expect(daily.trashPapers).toHaveBeenCalledWith([5]);
    expect(daily.addToLibrary).not.toHaveBeenCalled();
  }
  client.clear();
});

it('selects only the visible source-filtered slate for batch actions', async () => {
  const papers = [
    { item_id: 1, item_key: 'P1', stable_feed_key: 'feed:P1', title: 'arXiv paper', feed_name: 'arXiv', review_ready: false },
    { item_id: 2, item_key: 'P2', stable_feed_key: 'feed:P2', title: 'PubMed paper', feed_name: 'PubMed' },
  ];
  let members = [memberFacts(papers[0], { review_ready: true }), memberFacts(papers[1])];
  daily.fetchDailySlate.mockResolvedValue({ papers, awaiting_review_total: 2 });
  daily.fetchDailyMembers.mockImplementation(async () => ({ members }));
  daily.getTriageStatus.mockResolvedValue({ running: false });
  daily.addToLibrary.mockImplementation(async () => {
    members = [{ ...members[0], decision: 'user_approved', zotero_sync_status: 'pending' }, members[1]];
    return { added: 1, pending_sync: 1, failed: [], failed_count: 0 };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);

  await waitFor(() => expect(screen.getByLabelText('Select arXiv paper').disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('Feed source'), { target: { value: 'arXiv' } });
  fireEvent.click(screen.getByLabelText('Select all'));
  expect(screen.getByRole('button', { name: 'Add to library' }).disabled).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Add to library' }));

  await waitFor(() => expect(daily.addToLibrary).toHaveBeenCalledWith([1], ''));
  expect(await screen.findByText(/Decision saved; Zotero delivery pending/)).toBeTruthy();
  // After the action the card is handled, selection clears and the bulk bar hides.
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Add to library' })).toBeNull());
  expect(daily.fetchDailyMembers).toHaveBeenCalledWith([1, 2], expect.any(AbortSignal));
  expect(daily.fetchDailySlate).toHaveBeenCalledTimes(1);
  expect(daily.trashPapers).not.toHaveBeenCalled();
  expect(daily.triggerTriageBacklog).not.toHaveBeenCalled();
  client.clear();
});

it('passes the actual slate policy to cards while preserving server order, scores and labels', async () => {
  const papers = [
    { item_id: 8, item_key: 'P8', title: 'Earlier lower composite', composite_score: 2.8,
      reading_priority: 'must_read', selection_evidence: { mode: 'quality_first',
        stored: { composite_recorded: true, reading_priority: 'must_read' } } },
    { item_id: 9, item_key: 'P9', title: 'Later higher composite', composite_score: 4.6,
      selection_evidence: { mode: 'quality_first', stored: { composite_recorded: true } } },
  ];
  const before = JSON.stringify(papers);
  daily.fetchDailySlate.mockResolvedValue({ papers, selection_policy: 'quality_first', awaiting_review_total: 2 });
  daily.fetchDailyMembers.mockResolvedValue({ members: papers.map(paper => memberFacts(paper)) });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);

  await screen.findByText('Earlier lower composite');
  const cards = screen.getAllByRole('article');
  expect(within(cards[0]).getByText('Earlier lower composite')).toBeTruthy();
  expect(within(cards[0]).getByText('Relevance: moderate')).toBeTruthy();
  expect(within(cards[1]).getByText('Later higher composite')).toBeTruthy();
  expect(within(cards[1]).getByText('Relevance: top pick')).toBeTruthy();
  expect(screen.getAllByText('Quality-first')).toHaveLength(2);
  fireEvent.click(within(cards[0]).getAllByText('more')[0]);
  expect(JSON.stringify(papers)).toBe(before);
  expect(daily.addToLibrary).not.toHaveBeenCalled();
  expect(daily.trashPapers).not.toHaveBeenCalled();
  client.clear();
});

it.each([null, { running: true, gate_onward: 336 }])(
  'reports unavailable triage status instead of presenting stale progress (%j)', async (previous) => {
    daily.fetchDailySlate.mockResolvedValue({ papers: [], awaiting_review_total: 0 });
    daily.getTriageStatus.mockRejectedValue(new Error('Server unavailable'));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    if (previous) client.setQueryData(['triage-status'], previous);
    render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);

    const button = await screen.findByRole('button', { name: 'Triage status unavailable' });
    expect(button.disabled).toBe(true);
    expect(screen.getByRole('alert').textContent).toContain('Server unavailable');
    expect(screen.queryByText(/Triaging.*kept/)).toBeNull();
    expect(screen.queryByText(/You're all caught up/)).toBeNull();
    expect(screen.queryByText(/All caught up — nothing new to cull/)).toBeNull();
    fireEvent.click(button);
    expect(daily.triggerTriageBacklog).not.toHaveBeenCalled();

    daily.getTriageStatus.mockResolvedValue({ running: false, done: false });
    await act(() => client.refetchQueries({ queryKey: ['triage-status'] }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Triage backlog' }).disabled).toBe(false));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(daily.triggerTriageBacklog).not.toHaveBeenCalled();
    client.clear();
  },
);

it('keeps polling a failed initial status read and recovers without starting triage', async () => {
  vi.useFakeTimers();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  try {
    daily.fetchDailySlate.mockResolvedValue({ papers: [], awaiting_review_total: 0 });
    daily.getTriageStatus.mockRejectedValue(new Error('Server unavailable'));
    render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);
    await act(() => vi.advanceTimersByTimeAsync(50));
    expect(screen.getByRole('button', { name: 'Triage status unavailable' }).disabled).toBe(true);

    daily.getTriageStatus.mockResolvedValue({ running: true, gate_onward: 337 });
    await act(() => vi.advanceTimersByTimeAsync(3050));
    expect(daily.getTriageStatus.mock.calls.length).toBeGreaterThan(1);
    expect(screen.getByRole('button', { name: 'Sorting the backlog… 337 kept' }).disabled).toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(daily.triggerTriageBacklog).not.toHaveBeenCalled();
  } finally {
    client.clear();
    vi.useRealTimers();
  }
});

it('blocks unreviewed Add but keeps Trash available', async () => {
  const paper = { item_id: 3, item_key: 'P3', stable_feed_key: 'feed:P3', title: 'Not reviewed', review_ready: true };
  daily.fetchDailySlate.mockResolvedValue({ papers: [paper], awaiting_review_total: 1 });
  daily.fetchDailyMembers.mockResolvedValue({ members: [memberFacts(paper)] });
  daily.getTriageStatus.mockResolvedValue({ running: false });
  daily.trashPapers.mockResolvedValue({ trashed: 1, failed: [], failed_count: 0 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Today /></MemoryRouter></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText('Select Not reviewed').disabled).toBe(false));
  fireEvent.click(screen.getByLabelText('Select all'));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add to library' }).disabled).toBe(true));
  expect(screen.getByText(/skipped by Add/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Trash' }));
  await waitFor(() => expect(daily.trashPapers.mock.calls[0][0]).toEqual([3]));
  expect(daily.addToLibrary).not.toHaveBeenCalled();
  client.clear();
});
