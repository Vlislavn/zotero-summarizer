// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import '../../testSetup.js';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { submitVerdict } from '../../api/goldenApi.js';
import { cacheReviewDetail, cachedResponse, pendingMutations, publishStatus, savePapers } from '../../offlineStore.js';
import PaperReviewPage from '../../pages/PaperReviewPage.jsx';
import ReadNextView from './ReadNextView.testkit.jsx';

const clients = [];
let browserOnline;
let tab;
beforeEach(async () => {
  browserOnline = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  onlineManager.setOnline(false);
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Synthetic Mac off')));
  tab = { opener: {}, document: { write: vi.fn() }, location: { href: '' }, close: vi.fn() };
  vi.spyOn(window, 'open').mockReturnValue(tab);
  await publishStatus('', 'connected');
});
afterEach(async () => {
  cleanup();
  clients.splice(0).forEach(client => client.clear());
  onlineManager.setOnline(true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  await publishStatus('', 'connected');
});

function mount(element, path = '/library') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '*', element },
  ], { initialEntries: [path] })} /></QueryClientProvider>);
}
function queue(item, onSaved = vi.fn()) {
  return mount(<ReadNextView items={[item]} totalUnread={1} rawCount={1}
    readHidden={0} loading={false} status="ready" modelReady computedAt="2026-10-01T00:00:00Z"
    collections={[]} selected={new Set()} onSaved={onSaved} />);
}
async function seed(key, hasPdf = false) {
  const paper = { item_key: key, title: `Cached paper ${key}`, has_pdf: hasPdf,
    reading_priority: 'should_read', revisions: { verdict: 7, review_note: 3 } };
  await savePapers([paper]);
  await cacheReviewDetail(key, detailOf(paper));
  return paper;
}
const detailOf = paper => ({ ...paper, abstract: 'Cached abstract', user_note: 'Preserved manual note',
  verdict: null, authors: [], tags: [], collections: [], questions: [] });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
const outbox = async key => (await pendingMutations()).filter(row => row.item_key === key);

it('opens cached context in the same tab from the expanded compact "Open full review" link', async () => {
  const key = 'OFFLINE:compact CTA';
  const paper = await seed(key);
  const router = createMemoryRouter([
    { path: '/library', element: <ReadNextView items={[paper]} totalUnread={1} rawCount={1} readHidden={0}
      loading={false} status="ready" modelReady computedAt="2026-10-01T00:00:00Z" collections={[]}
      selected={new Set()} onSaved={vi.fn()} /> },
    { path: '/paper/:itemKey', element: <PaperReviewPage /> },
  ], { initialEntries: ['/library?c=COLL'] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  fireEvent.click(screen.getByText(paper.title));
  fireEvent.click(await screen.findByRole('link', { name: 'Open full review ↗' }));

  expect(await screen.findByRole('heading', { name: paper.title })).toBeTruthy();
  expect(`${router.state.location.pathname}${router.state.location.search}`)
    .toBe(`/paper/${encodeURIComponent(key)}?autoRun=false`);
  expect(window.open).not.toHaveBeenCalled();
  expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/library/render/'))).toBe(false);
  expect(await outbox(key)).toEqual([]);
  expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Preserved manual note');
  fireEvent.click(screen.getByRole('link', { name: '← Back' }));
  // Back lands on the exact Library URL it left, the paper still open over its list.
  expect(await screen.findByRole('dialog', { name: paper.title })).toBeTruthy();
  expect(`${router.state.location.pathname}${router.state.location.search}`)
    .toBe(`/library?${new URLSearchParams({ c: 'COLL', open: key })}`);
});

it('opens the cached page from the compact link for a known Mac outage even while the browser is online', async () => {
  browserOnline.mockReturnValue(true);
  onlineManager.setOnline(true);
  await publishStatus('Server unavailable', 'unavailable');
  const paper = await seed('KNOWN_MAC_OUTAGE');
  queue(paper);
  fireEvent.click(screen.getByText(paper.title));
  const link = await screen.findByRole('link', { name: 'Open full review ↗' });
  expect(link.getAttribute('href')).toBe('/paper/KNOWN_MAC_OUTAGE?autoRun=false');
  expect(window.open).not.toHaveBeenCalled();
  expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/library/render/'))).toBe(false);
});

// Not interested (dont_read) is set from the expanded row's own verdict panel:
// the same path the full page uses, so the offline ordering and the tag-after-verdict
// rules live in one place. A paper that already has a verdict opens its editor first.
async function markNotInterested(paper) {
  fireEvent.click(screen.getByText(paper.title));
  await screen.findByRole('link', { name: 'Open full review ↗' });
  const edit = screen.queryByRole('button', { name: 'Edit' });
  if (edit) fireEvent.click(edit);
  fireEvent.click(await screen.findByRole('button', { name: 'Not interested' }));
  fireEvent.click(screen.getByRole('button', { name: /^(Save verdict|Update)$/ }));
}

it('saves offline Not interested after an earlier verdict as one ordered durable intent, not a retryable failure', async () => {
  const key = 'OFFLINE_NOT_INTERESTED';
  const paper = await seed(key);
  await submitVerdict({ item_key: key, user_priority: 'could_read', comment: 'Earlier intentional edit' });
  const previous = (await outbox(key))[0];
  queue(paper);

  await markNotInterested(paper);

  await waitFor(async () => expect((await outbox(key)).length).toBe(2));
  expect(screen.queryByText(/Couldn’t save/)).toBeNull();
  const rows = await outbox(key);
  expect(rows.map(row => [row.operation, row.value])).toEqual([['set', 'could_read'], ['set', 'dont_read']]);
  expect(rows[0]).toEqual(previous);
  expect(rows[1].sequence).toBe(previous.sequence + 1);
  expect(rows[1].mutation_id).not.toBe(previous.mutation_id);
  expect(rows.every(row => row.base_revision === 7)).toBe(true);
  expect((await cachedResponse(`review:${key}`))).toMatchObject({
    verdict: { user_priority: 'dont_read' }, user_note: 'Preserved manual note',
  });
  expect(fetch.mock.calls.some(([url]) => url === '/api/library/reject-tag')).toBe(false);
});

it('queues the reject tag once after an online Not interested verdict and never repeats the verdict when the tag fails', async () => {
  browserOnline.mockReturnValue(true);
  onlineManager.setOnline(true);
  const key = 'ONLINE_NOT_INTERESTED_TAG_FAILURE';
  const paper = await seed(key);
  fetch.mockImplementation(async url => {
    if (url.startsWith('/api/golden/review-detail')) return json(detailOf(paper));
    return url === '/api/golden/verdict'
      ? json({ verdict: { user_priority: 'dont_read' } })
      : json({ detail: 'Synthetic tag unavailable' }, 503);
  });
  const onSaved = vi.fn();
  queue(paper, onSaved);

  await markNotInterested(paper);

  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  const writes = fetch.mock.calls.map(([url]) => url)
    .filter(url => url === '/api/golden/verdict' || url === '/api/library/reject-tag');
  expect(writes).toEqual(['/api/golden/verdict', '/api/library/reject-tag']);
  expect(await outbox(key)).toEqual([]);
});

it('keeps a primary verdict rejection retryable without claiming a device save or queuing the tag', async () => {
  const key = 'REMOVE_PRIMARY_REJECTED';
  const paper = await seed(key);
  fetch.mockImplementation(async url => url.startsWith('/api/golden/review-detail')
    ? json(detailOf(paper)) : json({ detail: 'Synthetic verdict rejected' }, 403));
  queue(paper);

  await markNotInterested(paper);

  expect(await screen.findByText(/HTTP 403/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Save verdict' }).disabled).toBe(false);
  expect(screen.queryByText(/Saved on this device/)).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url === '/api/library/reject-tag')).toBe(false);
  expect(await outbox(key)).toEqual([]);
});
