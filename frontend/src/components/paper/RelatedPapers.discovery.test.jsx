// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import '../../testSetup.js';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, focusManager, onlineManager } from '@tanstack/react-query';
import { createMemoryRouter, Link, Route, RouterProvider, Routes, useLocation, useParams } from 'react-router-dom';
import RelatedPapers from './RelatedPapers.jsx';
import PaperReviewPage from '../../pages/PaperReviewPage.jsx';
import Search from '../../pages/Search.jsx';

const fp = 'a'.repeat(64), token = 'b'.repeat(64);
const boundary = { enabled: true, token, provider: 'fixture-provider', model: 'fixture-model', endpoint: 'https://example.test/v1' };
const metadata = (state = 'saved') => ({ seed_key: 'SEED0001', seed_title: 'Seed', admission: { state, basis: state === 'saved' ? 'native' : null },
  context_fingerprint: state === 'saved' ? fp : null, boundary, private_context: { status: 'observed', record_count: 1 }, reason: null });
const reply = (dto, status = 200) => new Response(JSON.stringify(dto), { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const clients = [];

afterEach(() => {
  cleanup(); clients.splice(0).forEach(client => client.clear());
  focusManager.setFocused(undefined); onlineManager.setOnline(true);
  sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();
});

function Location() { const location = useLocation(); return <output aria-label="Location">{location.pathname}{location.search}</output>; }
function Seed() { const { itemKey } = useParams(); return <RelatedPapers key={itemKey} itemKey={itemKey} />; }
function mount(handler, { initial = '/paper/SEED0001', realPage = false } = {}) {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn((path, init = {}) => { requests.push({ path, init }); return handler(path, init); }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const view = render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '*', element: <><Link to="/paper/NEXT0001">Next fixture seed</Link><Location />
      <Routes><Route path="/paper/:itemKey" element={realPage ? <PaperReviewPage /> : <Seed />} />
        <Route path="/search" element={<Search />} /></Routes></> },
  ], { initialEntries: [initial] })} /></QueryClientProvider>);
  return { requests, ...view };
}

it('loads metadata independently of saved-related disclosure and never retries on focus/reconnect', async () => {
  const { requests } = mount(async path => {
    if (path === '/api/library/related/SEED0001/discover') return reply(metadata());
    throw new Error(`Unexpected: ${path}`);
  });
  await screen.findByRole('button', { name: 'Find new relevant' });

  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true); onlineManager.setOnline(false); onlineManager.setOnline(true); });

  expect(requests.map(r => r.path)).toEqual(['/api/library/related/SEED0001/discover']);
  expect(requests[0].init.method).toBeUndefined();
  expect(screen.getByRole('button', { name: 'Show related saved papers' })).toBeTruthy();
});

it.each(['not_saved', 'unknown'])('requires positive server saved admission, not key shape: %s', async state => {
  const { requests } = mount(async () => reply(metadata(state)));

  await screen.findByText(state === 'unknown' ? 'Saved membership unknown; discovery is unavailable.' : 'Save this paper before discovery.');

  expect(screen.queryByRole('button', { name: 'Find new relevant' })).toBeNull();
  expect(requests).toHaveLength(1);
  expect(requests[0].init.method).toBeUndefined();
});

it.each([{}, { ...metadata(), seed_key: 'OTHER000' }, { ...metadata(), admission: {} }, { ...metadata(), boundary: null }])(
  'malformed/wrong-seed metadata is a visible read error, never saved authority: %j', async dto => {
    const { requests } = mount(async () => reply(dto));

    await screen.findByRole('alert');

    expect(screen.queryByRole('button', { name: 'Find new relevant' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry discovery eligibility' })).toBeTruthy();
    expect(requests).toHaveLength(1);
    expect(requests[0].init.method).toBeUndefined();
  },
);

it.each(['saved', 'not_saved'])('a failed saved-library read cannot decide independent discovery admission: %s', async state => {
  const { requests } = mount(async path => path.endsWith('/discover') ? reply(metadata(state))
    : reply({ message: 'fixture local corpus unavailable' }, 503));
  await screen.findByText(state === 'saved' ? 'Find new relevant' : 'Save this paper before discovery.');
  fireEvent.click(screen.getByRole('button', { name: 'Show related saved papers' }));
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('fixture local corpus unavailable');
  expect(alert.textContent).toContain('Review remains usable; previous comments stay hidden until a fresh read succeeds.');
  expect(Boolean(screen.queryByRole('button', { name: 'Find new relevant' }))).toBe(state === 'saved');
  expect(requests.map(row => row.path)).toEqual(['/api/library/related/SEED0001/discover', '/api/library/related/SEED0001']);
  expect(requests.every(row => !row.init.method)).toBe(true);
});

it('saved admission with unavailable context is disabled and explicitly refreshable', async () => {
  const { requests } = mount(async () => reply({ ...metadata(), context_fingerprint: null, reason: 'source_unavailable' }));

  const find = await screen.findByRole('button', { name: 'Find new relevant' });

  expect(find.disabled).toBe(true);
  expect(screen.getByText('Discovery context unavailable: source_unavailable.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh discovery eligibility' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests.every(r => !r.init.method)).toBe(true);
});

it('eligibility failure remains visible until explicit GET retry', async () => {
  let reads = 0;
  const { requests } = mount(async () => ++reads === 1 ? reply({ message: 'fixture metadata offline' }, 503) : reply(metadata()));
  await screen.findByRole('alert');
  await act(async () => { focusManager.setFocused(true); onlineManager.setOnline(true); });
  expect(requests).toHaveLength(1);

  fireEvent.click(screen.getByRole('button', { name: 'Retry discovery eligibility' }));

  await screen.findByRole('button', { name: 'Find new relevant' });
  expect(requests).toHaveLength(2);
  expect(requests.every(r => !r.init.method)).toBe(true);
});

it.each(['default', 'declined', 'confirmed'])('captures actual public/comment consent request for %s', async consent => {
  const confirm = vi.fn(() => consent === 'confirmed');
  vi.stubGlobal('confirm', confirm);
  const { requests } = mount(async (path, init) => {
    if (!init.method) return reply(metadata());
    return reply({ message: 'fixture refusal' }, 409);
  });
  const find = await screen.findByRole('button', { name: 'Find new relevant' });
  if (consent !== 'default') {
    fireEvent.click(screen.getByText('Discovery processing and optional comments'));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Use private seed comments — experimental' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toBe('Send complete private seed comments and your reading verdict/rationale only to fixture-provider / fixture-model (https://example.test/v1)? Providers still receive public metadata only. Full context and results stay locally. Experimental usefulness is unqualified.');
  }
  expect(requests.filter(r => r.init.method === 'POST')).toEqual([]);

  fireEvent.click(find);

  await screen.findByRole('alert');
  const posts = requests.filter(r => r.init.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0].init.body)).toEqual({ context_fingerprint: fp, consent_token: consent === 'confirmed' ? token : null, retry: false });
  expect(posts[0].init.signal.aborted).toBe(false);
  expect(screen.getByLabelText('Location').textContent).toBe('/paper/SEED0001');
});

it('POST refusal permits explicit refreshed fingerprint and retry but clears private consent', async () => {
  vi.stubGlobal('confirm', () => true);
  let reads = 0;
  const { requests } = mount(async (path, init) => init.method === 'POST' ? reply({ message: 'fixture changed context' }, 409)
    : reply({ ...metadata(), context_fingerprint: ++reads === 1 ? fp : 'd'.repeat(64) }));
  await screen.findByRole('button', { name: 'Find new relevant' });
  fireEvent.click(screen.getByText('Discovery processing and optional comments'));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Use private seed comments — experimental' }));
  fireEvent.click(screen.getByRole('button', { name: 'Find new relevant' }));
  await screen.findByRole('alert');
  expect(requests.filter(r => r.init.method === 'POST')).toHaveLength(1);

  fireEvent.click(screen.getByRole('button', { name: 'Refresh discovery eligibility' }));
  const find = await screen.findByRole('button', { name: 'Find new relevant' });
  fireEvent.click(screen.getByText('Discovery processing and optional comments'));
  expect(screen.getByRole('checkbox', { name: 'Use private seed comments — experimental' }).checked).toBe(false);
  fireEvent.click(find);

  await waitFor(() => expect(requests.filter(r => r.init.method === 'POST')).toHaveLength(2));
  expect(JSON.parse(requests.filter(r => r.init.method === 'POST')[1].init.body)).toEqual({
    context_fingerprint: 'd'.repeat(64), consent_token: null, retry: true,
  });
});

it.each(['GET', 'POST'])('navigation aborts and fences late discovery %s', async method => {
  const held = deferred();
  const { requests } = mount((path, init) => {
    if (path === '/api/library/related/NEXT0001/discover') return Promise.resolve(reply({ ...metadata('unknown'), seed_key: 'NEXT0001' }));
    if ((init.method || 'GET') === method) return held.promise;
    return Promise.resolve(reply(metadata()));
  });
  if (method === 'POST') fireEvent.click(await screen.findByRole('button', { name: 'Find new relevant' }));
  await waitFor(() => expect(requests.some(r => (r.init.method || 'GET') === method)).toBe(true));
  const old = requests.find(r => (r.init.method || 'GET') === method);

  fireEvent.click(screen.getByRole('link', { name: 'Next fixture seed' }));
  await screen.findByText('Saved membership unknown; discovery is unavailable.');
  await act(async () => held.resolve(reply(method === 'GET' ? metadata() : { id: 'obsolete' })));

  expect(old.init.signal.aborted).toBe(true);
  expect(screen.getByLabelText('Location').textContent).toBe('/paper/NEXT0001');
  expect(screen.queryByRole('button', { name: 'Find new relevant' })).toBeNull();
});

it('real Search retry link returns to fresh seed admission/consent and requires a new click', async () => {
  const session = { id: 'discovery-retained', status: 'discovery_failed', candidates: [], discovery: {
    version: 1, seed_key: 'SEED0001', seed_title: 'Seed', context_fingerprint: fp, boundary,
    freshness: 'current', retrieval: { status: 'failed', empty_reason: null, variants: [] },
    assessment: { status: 'not_requested', items: [], submitted_context_ids: [] }, recommended_ids: [], unresolved_membership: [],
    seed_comments: null, comments_in_ranking: false, new_actions_allowed: false, error: { stage: 'retrieval', code: 'transport_error' },
  } };
  const { requests } = mount(async (path, init) => {
    if (path === '/api/search/discovery-retained') return reply(session);
    if (path === '/api/zotero/collections') return reply({ items: [] });
    if (path === '/api/library/related/SEED0001/discover') return reply(init.method === 'POST' ? { id: session.id } : metadata());
    throw new Error(`Unexpected: ${path}`);
  });
  fireEvent.click(await screen.findByRole('button', { name: 'Find new relevant' }));
  await screen.findByRole('link', { name: 'Return to seed to explicitly retry discovery' });
  expect(screen.getByLabelText('Location').textContent).toBe('/search?session=discovery-retained');

  fireEvent.click(screen.getByRole('link', { name: 'Return to seed to explicitly retry discovery' }));
  const find = await screen.findByRole('button', { name: 'Find new relevant' });

  expect(requests.filter(r => r.init.method === 'POST')).toHaveLength(1);
  expect(requests.filter(r => r.path.endsWith('/discover') && !r.init.method)).toHaveLength(2);
  fireEvent.click(screen.getByText('Discovery processing and optional comments'));
  expect(screen.getByRole('checkbox', { name: 'Use private seed comments — experimental' }).checked).toBe(false);
  fireEvent.click(find);
  await waitFor(() => expect(requests.filter(r => r.init.method === 'POST')).toHaveLength(2));
  expect(JSON.parse(requests.filter(r => r.init.method === 'POST')[1].init.body)).toEqual({ context_fingerprint: fp, consent_token: null, retry: true });
});

it.each(['GET', 'POST'])('real primary verdict ACK aborts late discovery %s before secondary completion', async method => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const held = deferred(), tag = deferred(), detailRefresh = deferred();
  const detail = { item_key: 'P1', title: 'Paper', tags: [], collections: [], has_pdf: false,
    verdict: { id: 1, item_key: 'P1', user_priority: 'dont_read', comment: 'Old verdict' } };
  let detailReads = 0, metadataReads = 0;
  const { requests } = mount(async (path, init) => {
    if (path === '/api/golden/review-detail?item_key=P1') return ++detailReads === 1 ? reply(detail) : detailRefresh.promise;
    if (path === '/api/library/related/P1/discover') {
      if (init.method === 'POST' || (method === 'GET' && ++metadataReads === 1)) return held.promise;
      return reply({ ...(method === 'POST' && metadataReads++ === 0 ? metadata() : metadata('unknown')), seed_key: 'P1' });
    }
    if (path === '/api/golden/verdict' && init.method === 'POST') return reply({ ...detail.verdict, ...JSON.parse(init.body) });
    if (path === '/api/library/reject-tag' && init.method === 'POST') return tag.promise;
    if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') return reply({ items: [] });
    if (path === '/api/library/render/P1' || path === '/api/library/deep-review/status?item_key=P1') return reply({ status: 'idle' });
    if (path === '/api/admin/llm-reachability') return reply({ stages: [{ stage: 'deep_review', enabled: false }] });
    throw new Error(`Unexpected: ${path}`);
  }, { initial: '/paper/P1', realPage: true });
  if (method === 'POST') fireEvent.click(await screen.findByRole('button', { name: 'Find new relevant' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit', exact: true }));
  fireEvent.change(screen.getByPlaceholderText('Why? (free text — used to detect patterns later)'), { target: { value: 'New acknowledged verdict' } });
  fireEvent.click(screen.getByRole('button', { name: 'Update', exact: true }));
  await waitFor(() => expect(requests.some(r => r.path === '/api/library/reject-tag')).toBe(true));
  const obsolete = requests.find(r => r.path.endsWith('/discover') && (r.init.method || 'GET') === method);

  await act(async () => held.resolve(reply(method === 'GET' ? metadata() : { id: 'obsolete' })));

  expect(obsolete.init.signal.aborted).toBe(true);
  expect(screen.getByLabelText('Location').textContent).toBe('/paper/P1');
  expect(screen.queryByRole('button', { name: 'Find new relevant' })).toBeNull();
  expect(detailReads).toBe(1);
  await act(async () => tag.resolve(reply({ queued: true })));
  await waitFor(() => expect(detailReads).toBe(2));
  await act(async () => detailRefresh.resolve(reply(detail)));
});
