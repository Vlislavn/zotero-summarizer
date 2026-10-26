// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Link, MemoryRouter } from 'react-router-dom';
import Search from './Search.jsx';
import ordinaryWire from '../../../tests/fixtures/discovery-ordinary-wire.json';
import channelCandidates from '../components/paper/fixtures/discoveryChannelCandidates.json';

const reply = (dto, status = 200) => new Response(JSON.stringify(dto), { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function session(id = 'discovery-one', changes = {}) {
  return { id, status: 'discovery_ready', candidates: [1, 2, 3, 4, 5].map(n => ({
    candidate_id: `external:${n}`, title: `Candidate ${n}`, url: `https://example.test/paper/${n}`,
    in_library: n === 1 ? null : false, review: n === 2 ? { state: 'needs_full_text' } : {},
  })), discovery: {
    version: 1, seed_key: 'SEED0001', seed_title: 'Fixture seed', context_fingerprint: 'a'.repeat(64), candidate_fingerprint: 'b'.repeat(64),
    boundary: { provider: 'fixture-provider', model: 'fixture-model', enabled: true, token: 'c'.repeat(64) },
    freshness: 'current', retrieval: { status: 'observed', empty_reason: null, variants: [{ channel: 'openalex_lexical',
      outcome: { status: 'success_nonempty', count: 5, error_code: null } }] },
    assessment: { status: 'assessed', items: [{ item_key: 'external:1', sentence: 'Fixture metadata connection.',
      relevance: 'relevant', preference: 'compatible', used_context_ids: ['comment:cited'],
      evidence: { seed: { item_key: 'SEED0001', field: 'title', excerpt: 'Fixture seed', start: 0, end: 12 },
        candidate: { item_key: 'external:1', field: 'title', excerpt: 'Candidate 1', start: 0, end: 11 } },
    }], submitted_context_ids: ['comment:cited', 'comment:submitted'] },
    recommended_ids: ['external:3', 'external:1', 'external:2'], unresolved_membership: ['external:1'],
    seed_comments: { availability: 'available', app_state: 'observed', native_state: 'observed', records: [
      { id: 'comment:cited', text: 'Synthetic cited seed comment.', source: 'human_verdict', authorship: 'user' },
      { id: 'comment:submitted', text: 'Synthetic submitted seed comment.', source: 'native_note', authorship: 'uncertain' },
    ] }, comments_in_ranking: true, new_actions_allowed: true, error: null, ...changes,
  } };
}
function mount(handler, initial = '/search?session=discovery-one') {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn((path, init = {}) => {
    requests.push({ path, init });
    if (path === '/api/zotero/collections') return Promise.resolve(reply({ items: [] }));
    return handler(path, init);
  }));
  const view = render(<MemoryRouter initialEntries={[initial]}><Link to="/search?session=next">Next session</Link><Search /></MemoryRouter>);
  return { requests, ...view };
}
afterEach(() => {
  cleanup(); vi.useRealTimers();
  try { sessionStorage.clear(); } catch { /* explicit blocked-storage fixture */ }
  vi.unstubAllGlobals(); vi.restoreAllMocks();
});

it.each([false, true])('URL wins an old stored pointer, including blocked storage: %s', async blocked => {
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'old-stored-session' }));
  if (blocked) vi.stubGlobal('sessionStorage', {
    getItem() { throw new DOMException('Blocked', 'SecurityError'); },
    setItem() { throw new DOMException('Blocked', 'SecurityError'); },
    removeItem() { throw new DOMException('Blocked', 'SecurityError'); },
  });
  const { requests } = mount(async path => {
    expect(path).toBe('/api/search/discovery-one');
    return reply(session());
  });

  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });

  expect(requests.filter(r => r.path !== '/api/zotero/collections').map(r => r.path)).toEqual(['/api/search/discovery-one']);
  expect(requests.every(r => !r.init.method)).toBe(true);
});

it('shows three ordered recommendations, retains the pool, and separates review/unknown membership/evidence', async () => {
  const { requests } = mount(async () => reply(session()));
  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });

  expect(screen.getAllByRole('article', { hidden: true }).filter(row => !row.closest('details'))
    .map(row => row.getAttribute('aria-label'))).toEqual(['Candidate 3', 'Candidate 1', 'Candidate 2']);
  expect(screen.getByText('2 retained candidates ▾').closest('details').open).toBe(false);
  const first = within(screen.getByRole('article', { name: 'Candidate 1' }));
  expect(first.getByText('Membership unknown — not established new.')).toBeTruthy();
  expect(first.getByText('Not reviewed.')).toBeTruthy();
  expect(within(screen.getByRole('article', { name: 'Candidate 2' })).getByText('Full text unavailable after acquisition — not deep-reviewed.')).toBeTruthy();
  fireEvent.click(first.getByText('Metadata evidence'));
  expect(first.getByText('Quote membership is checked; entailment is not independently verified. Comments are context, not paper evidence.')).toBeTruthy();
  expect(first.getByText('Model-cited comment IDs: comment:cited.')).toBeTruthy();
  expect(first.getByText('seed · SEED0001 · title [0, 12]: “Fixture seed”')).toBeTruthy();
  fireEvent.click(screen.getByText('Comments · available'));
  expect(screen.getByText('Synthetic cited seed comment.')).toBeTruthy();
  expect(screen.getByText('human_verdict · user · comment:cited · cited by assessment')).toBeTruthy();
  expect(screen.getByText('native_note · uncertain · comment:submitted · submitted, not model-cited')).toBeTruthy();
  fireEvent.click(screen.getByText('2 retained candidates ▾'));
  expect(screen.getAllByRole('article')).toHaveLength(5);
  expect(requests.filter(r => r.init.method === 'POST')).toEqual([]);
});

it.each(['discovery', 'ordinary'])('canonical safe links preserve channel identifiers and no-effect admission in %s Search', async mode => {
  const rows = [...channelCandidates,
    { candidate_id: 'unsafe-with-doi', title: 'Unsafe URL with identifier fallback', url: 'javascript:alert(1)', doi: '10.1234/safe-fallback' },
    { candidate_id: 'unsupported', title: 'No supported identifier or address', url: 'data:text/html,unsafe', pmid: '../not-a-pmid', openalex_id: 'https://example.org/W123' },
    { candidate_id: 'arxiv-only', title: 'Synthetic arXiv identifier', arxiv_id: '2501.12345', url: '' },
    { candidate_id: 'existing-url', title: 'Existing HTTPS address', url: 'https://example.test/paper/unchanged' },
  ].map(row => ({ ...row, in_library: null, review: {} }));
  const dto = mode === 'discovery' ? session() : { ...structuredClone(ordinaryWire), id: 'discovery-one' };
  dto.candidates = rows;
  if (mode === 'discovery') {
    dto.discovery.recommended_ids = rows.slice(0, 3).map(row => row.candidate_id);
    dto.discovery.unresolved_membership = rows.map(row => row.candidate_id);
    dto.discovery.assessment = { status: 'not_requested', items: [], submitted_context_ids: [] };
    dto.discovery.comments_in_ranking = false;
  }
  const { requests } = mount(async () => reply(dto));
  await screen.findByRole('article', { name: rows[0].title });
  if (mode === 'discovery') fireEvent.click(screen.getByText('4 retained candidates ▾'));
  const expected = ['https://doi.org/10.1234/fixture-doi', 'https://pubmed.ncbi.nlm.nih.gov/12345678/',
    'https://openalex.org/W1234567890', 'https://doi.org/10.1234/safe-fallback', null,
    'https://arxiv.org/abs/2501.12345', 'https://example.test/paper/unchanged'];
  rows.forEach((row, index) => {
    const card = within(screen.getByRole('article', { name: row.title }));
    if (expected[index]) {
      const link = card.getByRole('link', { name: row.title });
      expect(link.getAttribute('href')).toBe(expected[index]);
      expect(link.getAttribute('target')).toBe('_blank');
      expect(link.getAttribute('rel')).toBe('noreferrer');
    } else {
      expect(card.queryByRole('link', { name: row.title })).toBeNull();
      expect(card.getByText('No supported link available.')).toBeTruthy();
    }
  });
  expect(requests.every(r => !r.init.method)).toBe(true);
});

it.each(['observed', 'partial', 'failed', 'unknown', 'not_attempted'])('renders typed retrieval %s without inferring it from candidate counts', async status => {
  const dto = session('discovery-one', { retrieval: { status, empty_reason: null, variants: [{ channel: 'openalex_semantic',
    outcome: { status: status === 'not_attempted' ? 'not_attempted' : 'unknown', count: null, error_code: 'network_disabled' } }] } });
  dto.candidates = []; dto.discovery.recommended_ids = [];
  const { requests } = mount(async () => reply(dto));

  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });

  expect(screen.getByLabelText('Retrieval status').textContent).toContain(`Retrieval: ${status}.`);
  expect(screen.queryByText('Scheduled providers successfully returned no candidates.')).toBeNull();
  fireEvent.click(screen.getByText('Provider observations'));
  expect(screen.getByText(`openalex_semantic · ${status === 'not_attempted' ? 'not_attempted' : 'unknown'} · count: unknown · network_disabled`)).toBeTruthy();
  expect(requests.every(r => !r.init.method)).toBe(true);
});

it.each([
  ['success_empty', 0, null], ['success_nonempty', 3, null], ['failed', null, 'rate_limited'],
  ['failed', null, 'parse_error'], ['not_attempted', null, 'network_disabled'], ['unknown', null, null],
])('discloses provider receipt %s/count %s/code %s literally', async (status, count, error_code) => {
  const dto = session('discovery-one', { retrieval: { status: 'partial', empty_reason: null,
    variants: [{ channel: 'openalex_lexical', outcome: { status, count, error_code } }] } });
  mount(async () => reply(dto));
  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });

  fireEvent.click(screen.getByText('Provider observations'));

  expect(screen.getByText(`openalex_lexical · ${status} · count: ${count ?? 'unknown'} · ${error_code || ''}`.trim())).toBeTruthy();
});

it('observed per-candidate review error is not a reviewed or missing-full-text placeholder', async () => {
  const dto = session();
  dto.candidates[0].review = { state: 'error', error: 'fixture review unavailable' };
  mount(async () => reply(dto));

  await screen.findByRole('article', { name: 'Candidate 1' });

  expect(within(screen.getByRole('article', { name: 'Candidate 1' })).getByRole('alert').textContent).toBe('Review failed: fixture review unavailable');
});

it.each([
  ['providers_empty', 'Scheduled providers successfully returned no candidates.'],
  ['all_known_saved', 'Observed candidates were all known saved.'],
])('only server empty reason %s establishes the qualified empty state', async (empty_reason, text) => {
  const dto = session('discovery-one', { retrieval: { status: 'observed', empty_reason, variants: [] }, recommended_ids: [] });
  dto.candidates = [];
  mount(async () => reply(dto));
  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });
  expect(screen.getByText(text)).toBeTruthy();
});

it.each(['not_requested', 'assessed', 'no_comments', 'no_candidates', 'disabled', 'budget_exceeded', 'context_unavailable', 'failed', 'interrupted', 'stale'])(
  'renders assessment %s literally, never as a successful Review', async status => {
    const dto = session('discovery-one', { assessment: { status, items: [], submitted_context_ids: [] }, comments_in_ranking: false });
    const { requests } = mount(async () => reply(dto));

    await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });

    expect(screen.getByLabelText('Assessment status').textContent).toContain(`Assessment: ${status}.`);
    expect(within(screen.getByRole('article', { name: 'Candidate 1' })).getByText('Not reviewed.')).toBeTruthy();
    expect(requests.every(r => !r.init.method)).toBe(true);
  },
);

it.each(['discovery_pending', 'discovery_running', 'discovery_ready', 'discovery_failed', 'discovery_interrupted'])(
  'shows session progress %s without starting work', async status => {
    const dto = { ...session(), status };
    const { requests } = mount(async () => reply(dto));
    await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });
    expect(screen.getByText(`Session: ${status} · Freshness: current`)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Review retained candidates' }).disabled).toBe(status !== 'discovery_ready');
    expect(requests.every(r => !r.init.method)).toBe(true);
  },
);

it.each(['stale', 'unverified'])('hides private postimages and disables effects when %s even if old fields remain populated', async freshness => {
  mount(async () => reply(session('discovery-one', { freshness, new_actions_allowed: true })));
  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });

  expect(screen.queryByText('Fixture metadata connection.')).toBeNull();
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  expect(screen.queryByText('Comments · available')).toBeNull();
  expect(screen.getByRole('button', { name: 'Review retained candidates' }).disabled).toBe(true);
  fireEvent.click(screen.getByText('5 retained candidates ▾'));
  expect(screen.getByRole('article', { name: 'Candidate 1' })).toBeTruthy();
  expect(screen.getAllByRole('button', { name: 'Add to library' }).every(button => button.disabled)).toBe(true);
});

it.each(['discovery_pending', 'discovery_running', 'discovery_failed', 'discovery_interrupted'])(
  'hides private projections and recommendations while Discovery is %s', async status => {
    mount(async () => reply({ ...session(), status }));
    await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });
    expect(screen.queryByText('Fixture metadata connection.')).toBeNull();
    expect(screen.queryByText('Comments · available')).toBeNull();
    expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
    expect(screen.getByText('5 retained candidates ▾').closest('details').open).toBe(false);
    expect(screen.getByRole('button', { name: 'Review retained candidates' }).disabled).toBe(true);
  },
);

it('a failed GET retains its pointer and offers exactly explicit Retry rather than auto-reset/retry', async () => {
  let reads = 0;
  const { requests } = mount(async () => {
    if (++reads === 1) throw new Error('fixture status offline');
    return reply(session());
  });
  await screen.findByRole('button', { name: 'Retry session status' });
  expect(requests).toHaveLength(1);
  expect(JSON.parse(sessionStorage.getItem('zs.searchSession')).id).toBe('discovery-one');

  fireEvent.click(screen.getByRole('button', { name: 'Retry session status' }));

  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });
  expect(requests.filter(r => r.path === '/api/search/discovery-one')).toHaveLength(2);
});

it('a wrong-session GET is a visible identity error, never an adopted private projection', async () => {
  mount(async () => reply(session('wrong-session')));

  await screen.findByRole('button', { name: 'Retry session status' });

  expect(screen.queryByRole('heading', { name: 'Find new relevant · Fixture seed' })).toBeNull();
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
});

it('same-mounted URL navigation aborts late GET and never adopts its private projection', async () => {
  const held = deferred();
  const next = session('next', { seed_title: 'Next seed' });
  const { requests } = mount(path => path === '/api/search/discovery-one' ? held.promise : Promise.resolve(reply(next)));
  await waitFor(() => expect(requests).toHaveLength(1));
  const oldSignal = requests[0].init.signal;

  fireEvent.click(screen.getByRole('link', { name: 'Next session' }));
  await screen.findByRole('heading', { name: 'Find new relevant · Next seed' });
  await act(async () => held.resolve(reply(session('discovery-one', { seed_title: 'Obsolete seed' }))));

  expect(oldSignal.aborted).toBe(true);
  expect(screen.queryByRole('heading', { name: 'Find new relevant · Obsolete seed' })).toBeNull();
  expect(screen.getByRole('heading', { name: 'Find new relevant · Next seed' })).toBeTruthy();
});

it('unmount aborts the current session GET and prevents late adoption', async () => {
  const held = deferred();
  const view = mount(() => held.promise);
  await waitFor(() => expect(view.requests).toHaveLength(1));
  const signal = view.requests[0].init.signal;

  view.unmount();
  await act(async () => held.resolve(reply(session())));

  expect(signal.aborted).toBe(true);
  expect(view.requests).toHaveLength(1);
  expect(screen.queryByRole('heading', { name: 'Find new relevant · Fixture seed' })).toBeNull();
});

it.each(['review', 'materialize'])('late explicit %s completion cannot update another session or issue an old GET', async action => {
  const held = deferred();
  const { requests } = mount((path, init) => {
    if (init.method === 'POST') return held.promise;
    return Promise.resolve(reply(path === '/api/search/next' ? session('next', { seed_title: 'Next seed' }) : session()));
  });
  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });
  fireEvent.click(action === 'review' ? screen.getByRole('button', { name: 'Review retained candidates' })
    : within(screen.getByRole('article', { name: 'Candidate 1' })).getByRole('button', { name: 'Add to library' }));
  await waitFor(() => expect(requests.some(r => r.init.method === 'POST')).toBe(true));
  expect(requests.find(r => r.init.method === 'POST').path).toBe(`/api/search/discovery-one/${action}`);
  if (action === 'materialize') expect(JSON.parse(requests.find(r => r.init.method === 'POST').init.body)).toEqual({ candidate_id: 'external:1', collection_key: null });

  fireEvent.click(screen.getByRole('link', { name: 'Next session' }));
  await screen.findByRole('heading', { name: 'Find new relevant · Next seed' });
  await act(async () => held.resolve(reply(action === 'review' ? { accepted: true, status: 'reviewing' } : { status: 'added', zotero_key: 'NEW00001' })));

  expect(requests.filter(r => r.path === '/api/search/discovery-one')).toHaveLength(1);
  expect(screen.queryByText('✓ In library')).toBeNull();
});

it('successful Add consumes server freshness rather than claiming remaining recommendations are current', async () => {
  let reads = 0;
  const { requests } = mount(async (path, init) => {
    if (init.method === 'POST') return reply({ status: 'added', zotero_key: 'NEW00001' });
    return reply(++reads === 1 ? session() : session('discovery-one', { freshness: 'stale', new_actions_allowed: false, seed_comments: null }));
  });
  await screen.findByRole('article', { name: 'Candidate 1' });

  fireEvent.click(within(screen.getByRole('article', { name: 'Candidate 1' })).getByRole('button', { name: 'Add to library' }));

  await screen.findByText('Session: discovery_ready · Freshness: stale');
  expect(requests.filter(r => r.path === '/api/search/discovery-one')).toHaveLength(2);
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  expect(screen.getByRole('button', { name: 'Review retained candidates' }).disabled).toBe(true);
  expect(requests.filter(r => r.init.method === 'POST')).toHaveLength(1);
});

it('post-Add freshness GET failure hides private evidence and does not optimistically certify success', async () => {
  let reads = 0;
  const view = mount(async (path, init) => {
    if (init.method === 'POST') return reply({ status: 'added', zotero_key: 'NEW00001' });
    if (++reads > 1) throw new Error('fixture freshness offline');
    return reply(session());
  });
  await screen.findByRole('article', { name: 'Candidate 1' });

  fireEvent.click(within(screen.getByRole('article', { name: 'Candidate 1' })).getByRole('button', { name: 'Add to library' }));

  await screen.findByRole('button', { name: 'Retry session status' });
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  expect(screen.queryByText('✓ In library')).toBeNull();
  expect(screen.getByRole('button', { name: 'Review retained candidates' }).disabled).toBe(true);
  expect(view.requests.filter(r => r.init.method === 'POST')).toHaveLength(1);
});

it('explicit Review polls its owner, stops on read failure and resumes only from explicit Retry', async () => {
  vi.useFakeTimers();
  let reads = 0;
  let view;
  await act(async () => { view = mount(async (path, init) => {
    if (init.method === 'POST') return reply({ accepted: true, status: 'reviewing' });
    reads += 1;
    if (reads === 3) throw new Error('fixture poll offline');
    return reply({ ...session(), status: reads === 2 ? 'reviewing' : reads > 3 ? 'reviewed' : 'discovery_ready' });
  }); });
  expect(view.requests.filter(r => r.init.method === 'POST')).toHaveLength(0);

  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Review retained candidates' })));
  await act(async () => vi.advanceTimersByTimeAsync(30_000));

  expect(reads).toBe(3);
  expect(screen.getByRole('button', { name: 'Retry session status' })).toBeTruthy();
  expect(screen.getByText('Status unavailable; the server may still be working. This is not a cancellation or successful completion.')).toBeTruthy();
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Retry session status' })));
  expect(reads).toBe(4);
  expect(screen.getByText('Session: reviewed · Freshness: current')).toBeTruthy();
  expect(view.requests.filter(r => r.init.method === 'POST').map(r => r.path)).toEqual(['/api/search/discovery-one/review']);
});

it('a stale retained plan reconciles only on a click while new actions and private evidence remain fenced', async () => {
  const dto = session('discovery-one', { freshness: 'stale', new_actions_allowed: false, recommended_ids: [] });
  dto.candidates[0].planned_zotero_key = 'ADDED001';
  let acknowledged = false;
  const view = mount(async (path, init) => {
    if (path === '/api/search/discovery-one') return reply({ ...dto, candidates: dto.candidates.map((c, i) =>
      acknowledged && i === 0 ? { ...c, materialized_zotero_key: 'ADDED001' } : c) });
    if (path === '/api/search/discovery-one/materialize') {
      expect(init.method).toBe('POST'); expect(JSON.parse(init.body).candidate_id).toBe('external:1'); acknowledged = true;
      return reply({ status: 'already_added', zotero_key: 'ADDED001' });
    }
    throw new Error(`Unexpected ${path}`);
  });
  await screen.findByRole('heading', { name: 'Find new relevant · Fixture seed' });
  expect(view.requests.filter(row => row.init.method === 'POST')).toEqual([]);
  fireEvent.click(screen.getByText('5 retained candidates ▾'));
  expect(screen.getByRole('button', { name: 'Review retained candidates' }).disabled).toBe(true);
  expect(screen.getAllByRole('button', { name: 'Add to library', exact: true }).every(b => b.disabled)).toBe(true);
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Reconcile retained Add' })));
  await screen.findByText('✓ In library');
  expect(screen.getByRole('region', { name: 'Discovery status' }).textContent).toContain('Freshness: stale');
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  expect(view.requests.filter(row => row.init.method === 'POST')).toHaveLength(1);
});

it('new owners retire obsolete loading authority as well as obsolete responses', async () => {
  const refresh = deferred(), next = deferred();
  let discoveryReads = 0, view;
  await act(async () => { view = mount((path, init) => {
    if (path === '/api/search/discovery-one') return ++discoveryReads === 1 ? Promise.resolve(reply(session())) : refresh.promise;
    if (path === '/api/search/next') return next.promise;
    if (path === '/api/search/screen') return Promise.resolve(reply({ ...ordinaryWire, id: 'new-owner', status: 'reviewed' }));
    throw new Error(`Unexpected ${path} ${init.method}`);
  }); });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Refresh session status' })));
  expect(screen.queryByText('Synthetic cited seed comment.')).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole('link', { name: 'Next session' })));
  fireEvent.change(screen.getByPlaceholderText('e.g. LLM agents for clinical decision support in oncology'), { target: { value: 'replacement topic' } });
  expect(screen.getByRole('button', { name: 'Search', exact: true }).disabled).toBe(false);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true })));
  await act(async () => { refresh.resolve(reply(session())); next.resolve(reply({ ...ordinaryWire, id: 'next', candidates: [{ candidate_id: 'obsolete', title: 'Obsolete replacement read' }] })); });
  expect(screen.getByRole('article', { name: 'Public ordinary candidate' })).toBeTruthy();
  expect(screen.queryByRole('article', { name: 'Obsolete replacement read' })).toBeNull();
  expect(screen.queryByRole('region', { name: 'Discovery status' })).toBeNull();
  const obsoleteReads = view.requests.filter(row => ['/api/search/discovery-one', '/api/search/next'].includes(row.path));
  expect(obsoleteReads.every(row => row.init.signal.aborted), JSON.stringify(obsoleteReads.map(row => ({
    path: row.path, aborted: row.init.signal.aborted,
  })))).toBe(true);
});

it('a held ordinary restoration never blocks a new explicit screen owner', async () => {
  const held = deferred();
  let view;
  await act(async () => { view = mount((path, init) => {
    if (path === '/api/search/ordinary-wire') return held.promise;
    if (path === '/api/search/screen') return Promise.resolve(reply({ ...ordinaryWire, id: 'ordinary-new', status: 'reviewed' }));
    throw new Error(`Unexpected ${path} ${init.method}`);
  }, '/search?session=ordinary-wire'); });
  fireEvent.change(screen.getByPlaceholderText('e.g. LLM agents for clinical decision support in oncology'), { target: { value: 'new explicit topic' } });
  expect(screen.getByRole('button', { name: 'Search', exact: true }).disabled).toBe(false);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true })));
  await act(async () => held.resolve(reply({ ...ordinaryWire, candidates: [{ candidate_id: 'old', title: 'Obsolete restored candidate' }] })));
  expect(view.requests.find(row => row.path === '/api/search/ordinary-wire').init.signal.aborted).toBe(true);
  expect(screen.queryByRole('article', { name: 'Obsolete restored candidate' })).toBeNull();
  expect(screen.getByRole('article', { name: 'Public ordinary candidate' })).toBeTruthy();
  expect(view.requests.filter(row => row.init.method === 'POST').map(row => row.path)).toEqual(['/api/search/screen']);
});

it.each(['screen', 'url', 'storage'])('actual ordinary HTTP wire stays ordinary through %s adoption and poll failure', async mode => {
  vi.useFakeTimers();
  expect(ordinaryWire).not.toHaveProperty('discovery');
  if (mode === 'storage') sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: ordinaryWire.id }));
  let reads = 0;
  await act(async () => {
    mount(async path => {
      if (path === '/api/search/screen') return reply(ordinaryWire);
      expect(path).toBe(`/api/search/${ordinaryWire.id}`);
      reads += 1;
      if (reads === (mode === 'screen' ? 1 : 2)) throw new Error('ordinary transient read failure');
      return reply({ ...ordinaryWire, status: reads > (mode === 'screen' ? 1 : 2) ? 'reviewed' : 'reviewing' });
    }, mode === 'url' ? `/search?session=${ordinaryWire.id}` : '/search');
  });
  if (mode === 'screen') {
    fireEvent.change(screen.getByPlaceholderText('e.g. LLM agents for clinical decision support in oncology'), { target: { value: ordinaryWire.raw_query } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true })));
  }
  expect(screen.queryByRole('region', { name: 'Discovery status' })).toBeNull();
  expect(screen.getByRole('article', { name: 'Public ordinary candidate' })).toBeTruthy();
  expect(screen.getByText('Sort by')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Add to library' }).disabled).toBe(false);
  await act(async () => vi.advanceTimersByTimeAsync(6000));
  expect(reads).toBe(mode === 'screen' ? 2 : 3);
  expect(screen.queryByText('ordinary transient read failure')).toBeNull();
  expect(screen.queryByText('Deep-reviewing the top papers…')).toBeNull();
  expect(screen.getByRole('button', { name: 'Add to library' }).disabled).toBe(false);
});

it('ordinary screen still auto-polls and starting a new screen actually invokes cancellation', async () => {
  vi.useFakeTimers();
  const held = deferred();
  let screenCount = 0, view;
  await act(async () => { view = mount((path, init) => {
    if (path === '/api/search/screen') return Promise.resolve(reply({ id: `ordinary-${++screenCount}`, status: 'reviewing', candidates: [] }));
    if (path === '/api/search/ordinary-1') return held.promise;
    if (path === '/api/search/ordinary-2') return Promise.resolve(reply({ id: 'ordinary-2', status: 'reviewed', candidates: [] }));
    throw new Error(`Unexpected ${path} ${init.method}`);
  }, '/search'); });
  fireEvent.change(screen.getByPlaceholderText('e.g. LLM agents for clinical decision support in oncology'), { target: { value: 'fixture query' } });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true })));
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  const old = view.requests.find(r => r.path === '/api/search/ordinary-1');

  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Search', exact: true })));
  await act(async () => held.resolve(reply({ id: 'ordinary-1', status: 'reviewing', candidates: [] })));
  await act(async () => vi.advanceTimersByTimeAsync(30_000));

  expect(old.init.signal.aborted).toBe(true);
  expect(view.requests.filter(r => r.path === '/api/search/ordinary-1')).toHaveLength(1);
  expect(view.requests.filter(r => r.path === '/api/search/ordinary-2')).toHaveLength(1);
  expect(view.requests.filter(r => r.init.method === 'POST').map(r => r.path)).toEqual(['/api/search/screen', '/api/search/screen']);
});
