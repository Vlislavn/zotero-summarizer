// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import Search from './Search.jsx';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  try { sessionStorage.clear(); } catch { /* storage may be blocked by the test */ }
  vi.unstubAllGlobals();
});

it('keeps native associated labels while both Search textareas contain values', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [] }))));
  await act(async () => render(<Search />));
  for (const name of ['Search query', 'Questions (optional)']) {
    const field = screen.getByLabelText(name);
    fireEvent.change(field, { target: { value: 'owned nonempty value' } });
    expect(field.labels).toHaveLength(1);
    expect(field.labels[0].control).toBe(field);
    expect(field.labels[0].classList.contains('sr-only')).toBe(false);
  }
});

it('shows every server query variant and files identical-title cards by their distinct IDs', async () => {
  const writes = [];
  const research = {
    id: 'search-test', status: 'reviewed',
    plan: {
      arxiv: 'broad topic', arxiv_variants: ['"precise topic"', 'broad topic'], openreview: 'peer-reviewed topic',
      display: [
        { source: 'arxiv', query: '"precise topic"' },
        { source: 'arxiv', query: 'broad topic' },
        { source: 'openreview', query: 'peer-reviewed topic' },
      ],
    },
    candidates: [{ candidate_id: 'local:first', title: 'Same title' }, { candidate_id: 'local:second', title: 'Same title' }],
  };
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: research.id }));
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let body;
    if (path === '/api/search/search-test') body = research;
    else if (path === '/api/zotero/collections') body = { items: [] };
    else if (path === '/api/search/search-test/materialize') {
      writes.push(JSON.parse(options.body));
      body = { status: 'added', zotero_key: `Z${writes.length}` };
    } else throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body));
  }));
  render(<Search />);
  await screen.findByText('Query plan (per source)');
  fireEvent.click(screen.getByText('Query plan (per source)'));
  expect(screen.getByText('"precise topic"')).toBeTruthy();
  expect(screen.getByText('broad topic')).toBeTruthy();
  expect(screen.getByText('peer-reviewed topic')).toBeTruthy();

  fireEvent.click(screen.getAllByRole('button', { name: 'Add to library' })[0]);
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Add to library' })).toHaveLength(1));
  fireEvent.click(screen.getByRole('button', { name: 'Add to library' }));
  await waitFor(() => expect(screen.getAllByText('✓ In library')).toHaveLength(2));
  expect(writes).toEqual([
    { candidate_id: 'local:first', collection_key: null },
    { candidate_id: 'local:second', collection_key: null },
  ]);
});

it('sorts the complete result pool and remembers relevance + prestige on remount', async () => {
  const research = { id: 'sorted', status: 'reviewed', candidates: [
    { candidate_id: 'a', title: 'Most relevant', url: 'https://example.com/a', query_score: 0.9, cited_by_count: 0 },
    { candidate_id: 'b', title: 'Well cited', url: 'https://example.com/b', query_score: 0.85, cited_by_count: 100, relevance_band: 'weak' },
  ] };
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: research.id }));
  vi.stubGlobal('fetch', vi.fn(async (path) => new Response(JSON.stringify(
    path === '/api/search/sorted' ? research : { items: [] },
  ))));
  const page = render(<Search />);
  const selector = await screen.findByRole('combobox', { name: 'Sort by' });
  fireEvent.change(selector, { target: { value: 'relevance_prestige' } });
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['Well cited', 'Most relevant']);
  expect(screen.queryByText(/weaker match/)).toBeNull();
  page.unmount();
  render(<Search />);
  expect((await screen.findByRole('combobox', { name: 'Sort by' })).value).toBe('relevance_prestige');
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['Well cited', 'Most relevant']);
});

it('keeps Search usable when session storage throws a SecurityError', async () => {
  vi.stubGlobal('sessionStorage', {
    getItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
    setItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
    removeItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
    clear: () => { throw new DOMException('Blocked', 'SecurityError'); },
  });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [] }))));

  render(<Search />);

  expect(await screen.findByRole('heading', { name: 'Targeted Search' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Search' }).disabled).toBe(true);
});

it('opts in explicitly and keeps a promoted weak candidate in the recommended visible set', async () => {
  const requests = [];
  const writes = [];
  const research = { id: 'evidence', status: 'reviewed', evidence_policy: 'question_evidence', questions: ['Question'],
    evidence_status: { state: 'complete', calls_used: 24, calls: 24, topic_floor: 0.35, promoted_candidate_ids: ['p11'] },
    candidates: [{ candidate_id: 'p11', title: 'Answer-bearing paper', url: 'https://example.org/11',
      query_score: 0.5, relevance_band: 'weak' },
    { candidate_id: 'p1', title: 'Higher topic match', url: 'https://example.org/1', query_score: 0.9 }],
  };
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let body = { items: [] };
    if (path === '/api/search/screen') { requests.push(JSON.parse(options.body)); body = research; }
    if (path === '/api/search/evidence/materialize') { writes.push(JSON.parse(options.body)); body = { zotero_key: 'SAVED' }; }
    return new Response(JSON.stringify(body));
  }));
  render(<Search />);
  const checkbox = screen.getByRole('checkbox', { name: /Experimental original-source/ });
  expect(checkbox.checked).toBe(false);
  fireEvent.change(screen.getByPlaceholderText(/e.g. LLM agents/), { target: { value: 'topic' } });
  fireEvent.change(screen.getByPlaceholderText(/one specific question/), { target: { value: 'Question' } });
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('Answer-bearing paper');
  expect(requests).toEqual([{ query: 'topic', questions: ['Question'], evidence_policy: 'question_evidence' }]);
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['Answer-bearing paper', 'Higher topic match']);
  expect(screen.queryByText(/weaker match/)).toBeNull();
  expect(screen.getByRole('status').textContent).toContain('1 candidates promoted');
  expect(writes).toEqual([]);
  fireEvent.change(screen.getByRole('combobox', { name: 'Sort by' }), { target: { value: 'relevance' } });
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['Higher topic match', 'Answer-bearing paper']);
  fireEvent.click(screen.getAllByRole('button', { name: 'Add to library' })[1]);
  await screen.findByText('✓ In library');
  expect(writes).toEqual([{ candidate_id: 'p11', collection_key: null }]);
});

it('ignores a late restored session after a new screen request', async () => {
  let restore;
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'old' }));
  vi.stubGlobal('fetch', vi.fn(async (path) => {
    if (path === '/api/search/old') return new Promise((resolve) => { restore = resolve; });
    return new Response(JSON.stringify(path === '/api/search/screen'
      ? { id: 'new', status: 'reviewed', candidates: [{ candidate_id: 'new', title: 'New result' }] } : { items: [] }));
  }));
  render(<Search />);
  fireEvent.change(screen.getByPlaceholderText(/e.g. LLM agents/), { target: { value: 'new topic' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('New result');
  await act(async () => {
    restore(new Response(JSON.stringify({ id: 'old', status: 'reviewed', candidates: [{ candidate_id: 'old', title: 'Old result' }] })));
  });
  expect(screen.getByText('New result')).toBeTruthy();
  expect(screen.queryByText('Old result')).toBeNull();
});

it('does not stamp a late explicit Add onto a different search session', async () => {
  let finishAdd;
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'first', q: 'topic' }));
  vi.stubGlobal('fetch', vi.fn(async (path) => {
    if (path === '/api/search/first/materialize') return new Promise((resolve) => { finishAdd = resolve; });
    const body = path === '/api/search/first' ? { id: 'first', status: 'reviewed', candidates: [{ candidate_id: 'same', title: 'First result' }] }
      : path === '/api/search/screen' ? { id: 'second', status: 'reviewed', candidates: [{ candidate_id: 'same', title: 'Second result' }] }
        : { items: [] };
    return new Response(JSON.stringify(body));
  }));
  render(<Search />);
  await screen.findByText('First result');
  fireEvent.click(screen.getByRole('button', { name: 'Add to library' }));
  await waitFor(() => expect(finishAdd).toBeTypeOf('function'));
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('Second result');
  await act(async () => { finishAdd(new Response(JSON.stringify({ zotero_key: 'OLDKEY' }))); });
  expect(screen.getByRole('button', { name: 'Add to library' })).toBeTruthy();
  expect(screen.queryByText('✓ In library')).toBeNull();
});

it.each(['reviewing', 'reviewed'].flatMap(status => [
  { status, beforeAck: false }, { status, beforeAck: true },
]))('fences a pre-Add $status poll after ACK, with before-ACK control: $beforeAck', async ({ status, beforeAck }) => {
  vi.useFakeTimers();
  const writes = [];
  const chosen = { candidate_id: 'chosen', title: 'Paper awaiting review' };
  const removed = { candidate_id: 'removed', title: 'Removed by later refinement' };
  const snapshot = (state, candidates) => ({ id: 'poll-race', status: state, candidates });
  const obsolete = snapshot(status, [{ ...chosen, review: { brief: { tldr: 'Pre-Add review snapshot' } } }, removed]);
  let reads = 0, release, oldSignal;
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'poll-race' }));
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/zotero/collections') return new Response(JSON.stringify({ items: [] }));
    if (path === '/api/search/poll-race/materialize') {
      writes.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ status: 'added', zotero_key: 'SAVED001' }));
    }
    expect(path).toBe('/api/search/poll-race');
    reads += 1;
    if (reads === 1) return new Response(JSON.stringify(snapshot('reviewing', [chosen, removed])));
    if (reads === 2) {
      oldSignal = options.signal;
      return new Promise(resolve => { release = () => resolve(new Response(JSON.stringify(obsolete))); });
    }
    return new Response(JSON.stringify(snapshot(reads === 3 ? 'reviewing' : 'reviewed', [
      { ...chosen, title: 'Enriched metadata, same candidate', materialized_zotero_key: 'SAVED001',
        review: { brief: { tldr: reads === 3 ? 'Fresh review progress' : 'Final review progress' } } },
      { candidate_id: 'new', title: 'New refinement candidate' },
    ])));
  }));
  await act(async () => render(<Search />));
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(reads).toBe(2);
  if (beforeAck) await act(async () => release());

  await act(async () => fireEvent.click(within(screen.getByRole('article', { name: chosen.title }))
    .getByRole('button', { name: 'Add to library' })));
  expect(within(screen.getByRole('article', { name: chosen.title })).getByText('✓ In library')).toBeTruthy();
  if (!beforeAck) {
    await act(async () => release());
    expect(screen.queryByText('Pre-Add review snapshot')).toBeNull();
    expect(oldSignal.aborted).toBe(true);
  }
  expect(within(screen.getByRole('article', { name: chosen.title })).queryByRole('button', { name: 'Add to library' })).toBeNull();
  expect(screen.getAllByText('✓ In library')).toHaveLength(1);
  if (beforeAck) {
    expect(writes).toEqual([{ candidate_id: 'chosen', collection_key: null }]);
    return;
  }

  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(reads).toBe(3);
  expect(screen.getByText('Fresh review progress')).toBeTruthy();
  const enriched = within(screen.getByRole('article', { name: 'Enriched metadata, same candidate' }));
  expect(enriched.getByText('✓ In library')).toBeTruthy();
  expect(enriched.queryByRole('button', { name: 'Add to library' })).toBeNull();
  expect(screen.queryByRole('article', { name: removed.title })).toBeNull();
  expect(screen.getByRole('article', { name: 'New refinement candidate' })).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(screen.getByText('Final review progress')).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(reads).toBe(4);
  expect(writes).toEqual([{ candidate_id: 'chosen', collection_key: null }]);
});

it.each([false, true])('fences a held explicit retry GET after Add ACK, with before-ACK control: %s', async beforeAck => {
  vi.useFakeTimers();
  const candidate = { candidate_id: 'chosen', title: 'Explicit retry candidate' };
  const snapshot = (status, saved = false) => ({ id: 'direct-read', status, candidates: [
    { ...candidate, ...(saved ? { materialized_zotero_key: 'SAVED001' } : {}) },
  ] });
  let reads = 0, writes = 0, finishRetry;
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'direct-read' }));
  vi.stubGlobal('fetch', vi.fn(async (path) => {
    if (path === '/api/zotero/collections') return new Response(JSON.stringify({ items: [] }));
    if (path === '/api/search/direct-read/materialize') {
      writes += 1;
      return new Response(JSON.stringify({ status: 'added', zotero_key: 'SAVED001' }));
    }
    expect(path).toBe('/api/search/direct-read');
    reads += 1;
    if (reads === 1) return new Response(JSON.stringify(snapshot('reviewing')));
    if (reads === 2) return new Response(JSON.stringify({ error: 'unavailable' }), { status: 503 });
    if (reads === 3) return new Promise(resolve => { finishRetry = () => resolve(new Response(JSON.stringify(snapshot('reviewed')))); });
    return new Response(JSON.stringify(snapshot(reads === 4 ? 'reviewing' : 'reviewed', true)));
  }));
  await act(async () => render(<Search />));
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(reads).toBe(2);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Retry session status' })));
  expect(reads).toBe(3);
  if (beforeAck) await act(async () => finishRetry());
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add to library' })));
  expect(screen.getByText('✓ In library')).toBeTruthy();
  if (!beforeAck) await act(async () => finishRetry());
  expect(screen.getByText('✓ In library')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Add to library' })).toBeNull();
  await act(async () => vi.advanceTimersByTimeAsync(6000));
  expect(reads).toBe(5);
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(reads).toBe(5);
  expect(writes).toBe(1);
});

it('keeps both pending Add owners when an explicit same-session status retry begins', async () => {
  vi.useFakeTimers();
  const candidates = [1, 2].map(n => ({ candidate_id: `direct-${n}`, title: `Direct pending ${n}` }));
  const snapshot = (saved = false) => ({ id: 'retry-siblings', status: saved ? 'reviewed' : 'reviewing',
    candidates: candidates.map((c, i) => ({ ...c, ...(saved ? { materialized_zotero_key: `SAVED00${i + 1}` } : {}) })) });
  let reads = 0, finishRetry;
  const finishAdds = [];
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'retry-siblings' }));
  vi.stubGlobal('fetch', vi.fn(async (path) => {
    if (path === '/api/zotero/collections') return new Response(JSON.stringify({ items: [] }));
    if (path === '/api/search/retry-siblings/materialize') return new Promise(resolve => {
      const n = finishAdds.length + 1;
      finishAdds.push(() => resolve(new Response(JSON.stringify({ status: 'added', zotero_key: `SAVED00${n}` }))));
    });
    expect(path).toBe('/api/search/retry-siblings');
    reads += 1;
    if (reads === 1) return new Response(JSON.stringify(snapshot()));
    if (reads === 2) return new Response(JSON.stringify({ error: 'unavailable' }), { status: 503 });
    if (reads === 3) return new Promise(resolve => { finishRetry = () => resolve(new Response(JSON.stringify(snapshot()))); });
    return new Response(JSON.stringify(snapshot(true)));
  }));
  await act(async () => render(<Search />));
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  await act(async () => screen.getAllByRole('button', { name: 'Add to library' }).forEach(button => fireEvent.click(button)));
  expect(finishAdds).toHaveLength(2);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Retry session status' })));
  await act(async () => { finishAdds[0](); finishAdds[1](); });
  expect(screen.getAllByText('✓ In library')).toHaveLength(2);
  await act(async () => finishRetry());
  expect(screen.getAllByText('✓ In library')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Add to library' })).toBeNull();
  expect(finishAdds).toHaveLength(2);
});

it.each(['reviewing', 'reviewed'])('independent Add acknowledgements retire pending polls from an initially %s session', async initialStatus => {
  vi.useFakeTimers();
  const candidates = [1, 2].map(n => ({ candidate_id: `pick-${n}`, title: `Concurrent pick ${n}` }));
  const acknowledged = new Set();
  const finishAdds = [], pendingReads = [], readSignals = [];
  const heldReads = initialStatus === 'reviewing' ? 2 : 1;
  let reads = 0;
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'parallel-adds' }));
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/zotero/collections') return new Response(JSON.stringify({ items: [] }));
    if (path === '/api/search/parallel-adds/materialize') {
      const id = JSON.parse(options.body).candidate_id;
      return new Promise(resolve => finishAdds.push(() => {
        acknowledged.add(id);
        resolve(new Response(JSON.stringify({ status: 'added', zotero_key: id === 'pick-1' ? 'SAVED001' : 'SAVED002' })));
      }));
    }
    expect(path).toBe('/api/search/parallel-adds');
    reads += 1;
    const dto = { id: 'parallel-adds', status: reads === 1 ? initialStatus : 'reviewed', candidates: candidates.map(c => ({
      ...c, ...(acknowledged.has(c.candidate_id) ? { materialized_zotero_key: c.candidate_id === 'pick-1' ? 'SAVED001' : 'SAVED002' } : {}),
    })) };
    if (reads > 1 && reads <= heldReads + 1) {
      readSignals.push(options.signal);
      return new Promise(resolve => pendingReads.push(() => resolve(new Response(JSON.stringify(dto)))));
    }
    return new Response(JSON.stringify(dto));
  }));
  await act(async () => render(<Search />));
  await act(async () => {
    screen.getAllByRole('button', { name: 'Add to library' }).forEach(button => fireEvent.click(button));
  });
  expect(finishAdds).toHaveLength(2);
  if (initialStatus === 'reviewing') await act(async () => vi.advanceTimersByTimeAsync(3000));
  await act(async () => finishAdds[0]());
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  await act(async () => finishAdds[1]());
  await act(async () => pendingReads.forEach(release => release()));

  expect(readSignals.map(signal => signal.aborted)).toEqual(Array(heldReads).fill(true));
  expect(screen.getAllByText('✓ In library')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Add to library' })).toBeNull();
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(reads).toBe(heldReads + 2);
  expect(screen.getAllByText('✓ In library')).toHaveLength(2);
  expect(fetch.mock.calls.filter(([path]) => path.endsWith('/materialize'))).toHaveLength(2);
});

it.each(['transport', 'conflict'])('a %s Add failure keeps planned identity unacknowledged and never replays the write', async failure => {
  vi.useFakeTimers();
  let reads = 0, writes = 0, release, pollSignal;
  sessionStorage.setItem('zs.searchSession', JSON.stringify({ id: 'uncertain-add' }));
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/zotero/collections') return new Response(JSON.stringify({ items: [] }));
    if (path === '/api/search/uncertain-add/materialize') {
      writes += 1;
      if (failure === 'transport') throw new Error('Write outcome unconfirmed');
      return new Response(JSON.stringify({ error: 'search_write_conflict', message: 'Conflicting retained identity' }), { status: 409 });
    }
    expect(path).toBe('/api/search/uncertain-add');
    const dto = { id: 'uncertain-add', status: ++reads === 1 ? 'reviewing' : 'reviewed', candidates: [
      { candidate_id: 'planned', title: 'Not acknowledged', planned_zotero_key: 'PLANNED1' },
    ] };
    if (reads === 2) {
      pollSignal = options.signal;
      return new Promise(resolve => { release = () => resolve(new Response(JSON.stringify(dto))); });
    }
    return new Response(JSON.stringify(dto));
  }));
  await act(async () => render(<Search />));
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add to library' })));

  expect(screen.queryByText('✓ In library')).toBeNull();
  expect(screen.getByText(failure === 'transport' ? 'Write outcome unconfirmed' : 'Conflicting retained identity')).toBeTruthy();
  expect(pollSignal.aborted).toBe(false);
  await act(async () => release());
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(reads).toBe(2);
  expect(writes).toBe(1);
  expect(screen.queryByText('✓ In library')).toBeNull();
  expect(screen.getByRole('button', { name: 'Add to library' }).disabled).toBe(false);
});

it('sends only explicitly confirmed constraints, not restrictions inferred from the topic', async () => {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (path === '/api/search/screen') {
      requests.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ id: 'confirmed', status: 'reviewed', candidates: [], plan: { display: [
        { source: 'confirmation required: study_types', query: 'review' },
        { source: 'retrieval europepmc', query: 'observations: 0; status: unknown' },
      ] } }));
    }
    return new Response(JSON.stringify({ items: [] }));
  }));
  render(<Search />);
  fireEvent.change(screen.getByPlaceholderText(/e.g. LLM agents/), { target: { value: 'Only reviews about devices' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toEqual({ query: 'Only reviews about devices', questions: [] });
  fireEvent.change(screen.getByLabelText('Only publication types'), { target: { value: ' Review\n\n' } });
  fireEvent.change(screen.getByLabelText('Excluded exact phrases'), { target: { value: 'animal' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].constraints).toEqual({ must_include: [], must_not_include: ['animal'], study_types: ['Review'] });
  fireEvent.click(screen.getByText('Query plan (per source)'));
  expect(screen.getByText('observations: 0; status: unknown')).toBeTruthy();
  expect(screen.getByText('confirmation required: study_types')).toBeTruthy();
});
