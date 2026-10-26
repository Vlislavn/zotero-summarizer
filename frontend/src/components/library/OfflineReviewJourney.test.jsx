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
import ReadNextView from './ReadNextView.jsx';

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
  await cacheReviewDetail(key, { ...paper, abstract: 'Cached abstract', user_note: 'Preserved manual note',
    verdict: null, authors: [], tags: [], collections: [], questions: [] });
  return paper;
}
const outbox = async key => (await pendingMutations()).filter(row => row.item_key === key);

it.each([
  ['row shortcut', true],
  ['expanded compact CTA', false],
])('opens cached context from the actual Library %s without preparing an artifact', async (entry, hasPdf) => {
  const key = `OFFLINE:${entry}`;
  const paper = await seed(key, hasPdf);
  const library = queue(paper);
  if (entry === 'expanded compact CTA') fireEvent.click(screen.getByText(paper.title));
  const button = await screen.findByRole('button', { name: entry === 'row shortcut'
    ? 'Open cached review' : 'Open full review ↗' });

  fireEvent.click(button);

  const path = `/paper/${encodeURIComponent(key)}`;
  expect(window.open).toHaveBeenCalledOnce();
  expect(window.open).toHaveBeenCalledWith(path, '_blank', 'noopener');
  expect(tab.document.write).not.toHaveBeenCalled();
  expect(tab.close).not.toHaveBeenCalled();
  expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/library/render/'))).toBe(false);
  expect(await outbox(key)).toEqual([]);
  library.unmount();
  mount(<Routes><Route path="/paper/:itemKey" element={<PaperReviewPage />} /></Routes>, path);
  expect(await screen.findByRole('heading', { name: paper.title })).toBeTruthy();
  expect(screen.getByRole('textbox', { name: 'Manual notes' }).value).toBe('Preserved manual note');
  expect((await cachedResponse(`review:${key}`)).abstract).toBe('Cached abstract');
});

it('uses cached-page navigation for a known Mac outage even while the browser is online', async () => {
  browserOnline.mockReturnValue(true);
  onlineManager.setOnline(true);
  await publishStatus('Server unavailable', 'unavailable');
  const paper = await seed('KNOWN_MAC_OUTAGE');
  queue(paper);
  fireEvent.click(screen.getByRole('button', { name: 'Open cached review' }));
  expect(window.open).toHaveBeenCalledOnce();
  expect(window.open).toHaveBeenCalledWith('/paper/KNOWN_MAC_OUTAGE', '_blank', 'noopener');
  expect(fetch).not.toHaveBeenCalled();
});

it('confirms offline Remove after an earlier verdict as one ordered durable intent, not a retryable failure', async () => {
  const key = 'OFFLINE_REMOVE_CONFIRM';
  const paper = await seed(key);
  await submitVerdict({ item_key: key, user_priority: 'could_read', comment: 'Earlier intentional edit' });
  const previous = (await outbox(key))[0];
  const onSaved = vi.fn();
  queue({ ...paper, proposed_verdict: { proposed: 'dont_read', confidence: 0.85, flags: [] } }, onSaved);

  fireEvent.click(screen.getByRole('button', { name: 'Confirm — Remove ❌' }));

  expect(await screen.findByText('Saved on this device — waiting to sync.')).toBeTruthy();
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
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
  const confirmed = screen.getByRole('button', { name: 'Confirmed — Remove ❌' });
  expect(confirmed.disabled).toBe(true);
  fireEvent.click(confirmed);
  expect(await outbox(key)).toEqual(rows);
  expect(onSaved).toHaveBeenCalledOnce();
});

it('reports an online tag failure as a warning after verdict success without offering a verdict retry', async () => {
  browserOnline.mockReturnValue(true);
  onlineManager.setOnline(true);
  const key = 'ONLINE_REMOVE_TAG_FAILURE';
  const paper = await seed(key);
  fetch.mockImplementation(async url => url === '/api/golden/verdict'
    ? new Response(JSON.stringify({ verdict: { user_priority: 'dont_read' } }))
    : new Response(JSON.stringify({ detail: 'Synthetic tag unavailable' }), { status: 503 }));
  const onSaved = vi.fn();
  queue({ ...paper, proposed_verdict: { proposed: 'dont_read', confidence: 0.85, flags: [] } }, onSaved);

  fireEvent.click(screen.getByRole('button', { name: 'Confirm — Remove ❌' }));

  expect(await screen.findByText(/Verdict saved, but reject tag was not queued: Synthetic tag unavailable/)).toBeTruthy();
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(screen.queryByText(/Couldn’t save/)).toBeNull();
  const confirmed = screen.getByRole('button', { name: 'Confirmed — Remove ❌' });
  expect(confirmed.disabled).toBe(true);
  fireEvent.click(confirmed);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/golden/verdict', '/api/library/reject-tag']);
  expect(await outbox(key)).toEqual([]);
});

it('keeps a primary verdict rejection retryable without claiming a device save or queuing the tag', async () => {
  const key = 'REMOVE_PRIMARY_REJECTED';
  const paper = await seed(key);
  fetch.mockResolvedValue(new Response(JSON.stringify({ detail: 'Synthetic verdict rejected' }), { status: 403 }));
  const onSaved = vi.fn();
  queue({ ...paper, proposed_verdict: { proposed: 'dont_read', confidence: 0.85, flags: [] } }, onSaved);

  fireEvent.click(screen.getByRole('button', { name: 'Confirm — Remove ❌' }));

  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Couldn’t save: HTTP 403'));
  expect(screen.getByRole('button', { name: 'Confirm — Remove ❌' }).disabled).toBe(false);
  expect(screen.queryByText('Saved on this device — waiting to sync.')).toBeNull();
  expect(onSaved).not.toHaveBeenCalled();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/golden/verdict']);
  expect(await outbox(key)).toEqual([]);
});
