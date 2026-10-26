// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import '../testSetup.js';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, Link, RouterProvider } from 'react-router-dom';
import InlineAnnotate from '../components/library/InlineAnnotate.jsx';
import PaperReviewPage from './PaperReviewPage.jsx';
import { cachedReviewDetail, pendingMutations, publishStatus } from '../offlineStore.js';

afterEach(async () => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); await publishStatus('', 'unchecked'); });

it('keeps Generate disabled and does not claim a review failed on an offline reload', async () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path) => {
    if (path === '/api/golden/review-detail?item_key=P1') return new Response(JSON.stringify({
      item_key: 'P1', title: 'Offline paper', tags: [], collections: [], has_pdf: false,
    }), { status: 200 });
    if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    if (path === '/api/library/render/P1') return new Response(JSON.stringify({ status: 'idle' }), { status: 200 });
    if (path.includes('deep-review/status') || path.includes('llm-reachability')) throw new Error('Failed to fetch');
    return new Response('{}', { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '/paper/:itemKey', element: <PaperReviewPage /> },
  ], { initialEntries: ['/paper/P1'] })} /></QueryClientProvider>);

  expect((await screen.findByRole('button', { name: 'Generate review' })).disabled).toBe(true);
  expect(document.querySelector('main').parentElement.className).toContain('xl:grid-cols-[minmax(0,1fr)_20rem]');
  await screen.findByText(/Offline — cached reviews remain readable/);
  expect(screen.queryByText(/Review failed/)).toBeNull();
  client.clear();
});

it.each([
  [{ digest: {} }, true],
  [{ digest: { read_decision: 'skim' } }, true],
  [{ digest: { tldr: 'Body' }, error: 'Incomplete attempt' }, true],
  [{ digest: null, needs_pdf: true }, true],
  [{ digest: { tldr: 'Body' } }, false],
  [{ digest: { executive_summary: 'Body' } }, false],
  [{ digest: { key_findings: ['Finding'] } }, false],
])('retains an explicit Generate action for an unusable cached review: %j', async (deep, needsRetry) => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  await publishStatus('', 'connected');
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const writes = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/golden/review-detail?item_key=P1') data = {
      item_key: 'P1', title: 'Cached paper', tags: [], collections: [], has_pdf: false,
      deep_review: { quality: { grade: 'B' }, ...deep },
    };
    else if (path === '/api/admin/llm-reachability') data = {
      stages: [{ stage: 'deep_review', enabled: true, reachable: true }],
    };
    else if (path === '/api/library/deep-review/run' && options.method === 'POST') {
      writes.push(JSON.parse(options.body));
      data = { status: 'running' };
    } else if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') data = { items: [] };
    else data = { status: 'idle' };
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '/paper/:itemKey', element: <PaperReviewPage /> },
  ], { initialEntries: ['/paper/P1'] })} /></QueryClientProvider>);
  await screen.findByRole('heading', { name: 'Cached paper' });
  expect(writes).toEqual([]);
  if (needsRetry) {
    const generate = await screen.findByRole('button', { name: 'Generate review' });
    await waitFor(() => expect(generate.disabled).toBe(false));
    fireEvent.click(generate);
    await waitFor(() => expect(writes).toEqual([{ item_key: 'P1' }]));
    await screen.findByText('Generating the review…');
    expect(screen.queryByRole('button', { name: 'Generate review' })).toBeNull();
  } else {
    expect(screen.queryByRole('button', { name: 'Generate review' })).toBeNull();
  }
  client.clear();
});

it.each([
  [true, true, true], [false, true, true], [true, false, true], [false, true, false],
])('status recovery is GET-only with cached=%s, AI-enabled=%s and prior-running=%s', async (cached, aiEnabled, wasRunning) => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  await publishStatus('', 'connected');
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const writes = [], statusReads = [];
  let statusReply = () => wasRunning
    ? new Response(JSON.stringify({ status: 'running' }))
    : new Response(JSON.stringify({ detail: 'Temporarily unavailable' }), { status: 503 });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') writes.push(path);
    if (path === '/api/library/deep-review/status?item_key=P1') {
      statusReads.push(path);
      return statusReply();
    }
    const data = path === '/api/golden/review-detail?item_key=P1'
      ? { item_key: 'P1', title: 'Status recovery paper', tags: [], collections: [], has_pdf: false,
        deep_review: cached ? { digest: { tldr: 'Retained review body' } } : null }
      : path === '/api/admin/llm-reachability'
        ? { stages: [{ stage: 'deep_review', enabled: aiEnabled, reachable: true }] }
        : path === '/api/library/related/P1/discover'
          ? { seed_key: 'P1', admission: { state: 'unknown', basis: null }, context_fingerprint: null }
          : path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300'
            ? { items: [] } : { status: 'idle' };
    return new Response(JSON.stringify(data));
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: '/paper/:itemKey', element: <PaperReviewPage /> }], {
    initialEntries: ['/paper/P1?autoRun=false'],
  });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  try {
    await screen.findByRole('heading', { name: 'Status recovery paper' });
    if (wasRunning) {
      await screen.findByText('Generating the review…');
      statusReply = () => new Response(JSON.stringify({ detail: 'Temporarily unavailable' }), { status: 503 });
      await act(async () => { await client.refetchQueries({ queryKey: ['deep-review-status', 'P1'] }); });
    }
    const zone = within(document.getElementById('review'));
    await zone.findByText(/Review status unavailable/);
    if (cached) expect(zone.getByText('Retained review body')).toBeTruthy();
    else {
      const generate = zone.getByRole('button', { name: 'Generate review' });
      expect(generate.disabled).toBe(true);
      fireEvent.click(generate);
    }
    expect(zone.queryByText(/Review failed/)).toBeNull();
    const retry = zone.getByRole('button', { name: 'Retry status' });
    expect(retry.disabled).toBe(false);

    let finish;
    statusReply = () => new Promise(resolve => { finish = resolve; });
    fireEvent.click(retry);
    const expectedReads = wasRunning ? 3 : 2;
    await waitFor(() => expect(statusReads).toHaveLength(expectedReads));
    if (wasRunning) {
      await waitFor(() => expect(retry.disabled).toBe(true));
      fireEvent.click(retry);
      expect(statusReads).toHaveLength(expectedReads);
    }
    if (cached) expect(zone.getByText('Retained review body')).toBeTruthy();
    else {
      const generate = zone.getByRole('button', { name: 'Generate review' });
      expect(generate.disabled).toBe(true);
      fireEvent.click(generate);
    }
    await act(async () => { finish(new Response(JSON.stringify({ status: 'ready' }))); });
    await waitFor(() => expect(zone.queryByRole('button', { name: 'Retry status' })).toBeNull());
    if (cached) expect(zone.getByText('Retained review body')).toBeTruthy();
    else await waitFor(() => expect(zone.getByRole('button', { name: 'Generate review' }).disabled).toBe(false));
    expect(writes).toEqual([]);
  } finally { client.clear(); router.dispose(); }
});

it.each([['?autoRun=false', false], ['', true]])('paper navigation %s preserves explicit review consent', async (query, automatic) => {
  const writes = [];
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  await publishStatus('', 'connected');
  vi.stubGlobal('localStorage', { getItem: () => '["P1","P2"]' });
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path.startsWith('/api/golden/review-detail?')) {
      const key = new URLSearchParams(path.split('?')[1]).get('item_key');
      data = { item_key: key, title: `Unreviewed PDF ${key}`, tags: [], collections: [], has_pdf: true };
    } else if (path === '/api/admin/llm-reachability') {
      data = { stages: [{ stage: 'deep_review', enabled: true, reachable: true }] };
    } else if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') {
      data = { items: [] };
    } else if (options.method === 'POST') {
      writes.push([path, JSON.parse(options.body)]);
      data = { status: 'running' };
    } else data = { status: 'idle' };
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '/paper/:itemKey', element: <PaperReviewPage /> },
  ], { initialEntries: [`/paper/P1${query}`] })} /></QueryClientProvider>);

  if (!automatic) {
    const generate = await screen.findByRole('button', { name: 'Generate review' });
    await waitFor(() => expect(generate.disabled).toBe(false));
    expect(writes).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));
    await screen.findByRole('heading', { name: 'Unreviewed PDF P2' });
    const nextGenerate = await screen.findByRole('button', { name: 'Generate review' });
    await waitFor(() => expect(nextGenerate.disabled).toBe(false));
    expect(writes).toEqual([]);
    fireEvent.click(nextGenerate);
  }
  await waitFor(() => expect(writes).toEqual([
    ['/api/library/deep-review/run', { item_key: automatic ? 'P1' : 'P2' }],
  ]));
  client.clear();
});

it('the single relevance heading counts supported goals, not merely retrieved passages', async () => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path) => {
    const goals = [
      { goal: 'A', retrieval_state: 'hit', relevant: true, summary: 'Supported A.' },
      { goal: 'B', retrieval_state: 'hit', relevant: true, summary: 'Supported B.' },
      { goal: 'C', retrieval_state: 'hit', relevant: false, summary: 'No support.' },
    ];
    const data = path === '/api/golden/review-detail?item_key=P1'
      ? { item_key: 'P1', title: 'Paper', tags: [], collections: [], has_pdf: false, deep_review: { goal_summaries: goals } }
      : path === '/api/admin/llm-reachability' ? { stages: [{ stage: 'deep_review', enabled: false }] }
        : path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300' ? { items: [] } : { status: 'idle' };
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '/paper/:itemKey', element: <PaperReviewPage /> },
  ], { initialEntries: ['/paper/P1'] })} /></QueryClientProvider>);

  expect(await screen.findByRole('heading', { name: 'Relevance — 2 of 3 goals addressed' })).toBeTruthy();
  expect(screen.queryByText('◎ 2/3 goals')).toBeNull();
  client.clear();
});

it.each(['page', 'compact'])('%s keeps verdict comments until an explicit edit is saved', async (surface) => {
  const writes = [], unexpected = [];
  const detail = {
    item_key: 'P1', title: 'Paper', authors: [], tags: [], collections: [], has_pdf: false,
    verdict: { id: 1, item_key: 'P1', user_priority: 'must_read', comment: 'critical rationale' },
  };
  // Valid JSON with the wrong shape must not break Prev/Next navigation.
  vi.stubGlobal('localStorage', { getItem: () => '{}' });
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/golden/review-detail?item_key=P1') data = detail;
    else if (path === '/api/library/related/P1/discover' && (!options.method || options.method === 'GET')) {
      data = { seed_key: 'P1', seed_title: 'Paper', admission: { state: 'unknown', basis: null }, context_fingerprint: null };
    }
    else if (path === '/api/zotero/collections') data = { items: [] };
    else if (path === '/api/zotero/tags?limit=300') data = { items: [] };
    else if (path === '/api/library/render/P1') data = { status: 'idle' };
    else if (path === '/api/library/deep-review/status?item_key=P1') data = { status: 'idle' };
    else if (path === '/api/admin/llm-reachability') data = { stages: [{ stage: 'deep_review', enabled: false }] };
    else if (path === '/api/golden/verdict' && options.method === 'POST') {
      const payload = JSON.parse(options.body);
      writes.push(payload);
      detail.verdict = { ...detail.verdict, ...payload };
      data = detail.verdict;
    } else {
      unexpected.push(path);
      return new Response('{}', { status: 500 });
    }
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={createMemoryRouter([{ path: '/paper/:itemKey', element: surface === 'page'
        ? <PaperReviewPage /> : <InlineAnnotate itemKey="P1" /> }], { initialEntries: ['/paper/P1'] })} />
    </QueryClientProvider>,
  );

  await screen.findByRole('heading', { name: 'Your verdict' });
  expect(screen.queryByRole('button', { name: 'Must read' })).toBeNull();
  if (surface === 'page') {
    const jump = screen.getByRole('link', { name: 'Your verdict' });
    expect(document.querySelector(jump.getAttribute('href')).contains(
      screen.getByRole('heading', { name: 'Your verdict' }),
    )).toBe(true);
    fireEvent.click(jump);
  }
  fireEvent.click(screen.getByRole('button', { name: 'Edit', exact: true }));
  const comment = screen.getByPlaceholderText(/^Why\?/);
  expect(comment.value).toBe('critical rationale');
  fireEvent.click(screen.getByRole('button', { name: 'Should read' }));
  fireEvent.change(comment, { target: { value: 'unsaved draft' } });
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(writes).toEqual([]);

  fireEvent.click(screen.getByRole('button', { name: 'Edit', exact: true }));
  expect(screen.getByPlaceholderText(/^Why\?/).value).toBe('critical rationale');
  fireEvent.click(screen.getByRole('button', { name: 'Could read' }));
  fireEvent.click(screen.getByRole('button', { name: 'Update' }));
  await waitFor(() => expect(writes).toEqual([
    { item_key: 'P1', user_priority: 'could_read', comment: 'critical rationale' },
  ]));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit', exact: true }));
  fireEvent.change(screen.getByPlaceholderText(/^Why\?/), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Update' }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1]).toEqual({ item_key: 'P1', user_priority: 'could_read', comment: '' });
  await screen.findByRole('button', { name: 'Edit', exact: true });
  expect(unexpected).toEqual([]);
  client.clear();
});

it.each([
  ['link', false, 'dirty'], ['link', true, 'dirty'],
  ['navigate', false, 'dirty'], ['navigate', true, 'dirty'],
  ['back', false, 'dirty'], ['back', true, 'dirty'],
  ['navigate', false, 'clean'], ['navigate', false, 'saved'], ['unload', false, 'dirty'],
  ['link', false, 'unconfirmed'], ['back', false, 'refused'], ['navigate', false, 'empty'],
])('note navigation %s / accept=%s / %s uses one route-owned guard', async (exit, accept, mode) => {
  await publishStatus('', 'connected');
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(accept);
  const detail = { item_key: 'GUARD001', title: 'Guarded paper', user_note: 'Saved note',
    has_pdf: false, tags: [], collections: [] };
  const writes = [], unexpected = [];
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/golden/review-detail?item_key=GUARD001') data = detail;
    else if (path === '/api/golden/review-note' && options.method === 'POST') {
      writes.push(JSON.parse(options.body));
      if (mode === 'empty') return new Response(null, { status: 204 });
      data = mode === 'unconfirmed' ? {} : mode === 'refused' ? { saved: false } : { saved: true, note_written: false };
      if (data.saved) detail.user_note = writes.at(-1).note;
    } else if (path === '/api/library/related/GUARD001/discover') data = {
      seed_key: 'GUARD001', admission: { state: 'unknown', basis: null }, context_fingerprint: null };
    else if (path === '/api/admin/llm-reachability') data = { stages: [{ stage: 'deep_review', enabled: false }] };
    else if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') data = { items: [] };
    else if (path === '/api/library/render/GUARD001' || path === '/api/library/deep-review/status?item_key=GUARD001') data = { status: 'idle' };
    else { unexpected.push(path); return new Response('{}', { status: 500 }); }
    return new Response(JSON.stringify(data));
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([
    { path: '/library', element: <h1>Library destination</h1> },
    { path: '/paper/:itemKey', element: <><Link to="/library">Leave paper</Link><PaperReviewPage /></> },
  ], { initialEntries: ['/library', '/paper/GUARD001'], initialIndex: 1 });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  try {
    const notes = await screen.findByRole('textbox', { name: 'Manual notes' });
    if (mode !== 'clean') fireEvent.change(notes, { target: { value: 'Unsaved guard draft' } });
    const attemptedSave = ['saved', 'unconfirmed', 'refused', 'empty'].includes(mode);
    const dirty = !['clean', 'saved'].includes(mode);
    if (attemptedSave) {
      fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
      await waitFor(() => expect(writes).toEqual([{ item_key: 'GUARD001', note: 'Unsaved guard draft' }]));
      if (dirty) await screen.findByText(/Save failed:/);
      await waitFor(() => expect(screen.getByRole('button', { name: 'Save', exact: true }).disabled).toBe(!dirty));
    }
    if (exit === 'unload') {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(confirm).not.toHaveBeenCalled();
    } else {
      await act(async () => {
        if (exit === 'link') fireEvent.click(screen.getByRole('link', { name: 'Leave paper' }));
        else await router.navigate(exit === 'back' ? -1 : '/library');
      });
      if (dirty) {
        await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
        expect(confirm).toHaveBeenCalledWith('You have unsaved notes on this paper. Leave without saving them?');
      } else expect(confirm).not.toHaveBeenCalled();
      await waitFor(() => expect(router.state.location.pathname).toBe(
        dirty && !accept ? '/paper/GUARD001' : '/library'));
      if (dirty && !accept) expect(notes.value).toBe('Unsaved guard draft');
    }
    expect(writes).toHaveLength(attemptedSave ? 1 : 0);
    expect(unexpected).toEqual([]);
  } finally { client.clear(); router.dispose(); }
});

it.each(['failed', 'abstained'])('captures a question without PDF/render through the real page when QA %s', async outcome => {
  const itemKey = outcome === 'failed' ? 'PAPERQ01' : 'PAPERQ02';
  const question = 'Keep this question before paper text is available';
  const detail = { item_key: itemKey, title: 'Paper without source', has_pdf: false,
    user_note: 'Separate manual note', questions: [], tags: [], collections: [] };
  const writes = [], unexpected = [];
  let localAtServerSave;
  await publishStatus('', 'connected');
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') writes.push([path, JSON.parse(options.body)]);
    let data;
    if (path === `/api/golden/review-detail?item_key=${itemKey}`) data = detail;
    else if (path === '/api/library/questions' && options.method === 'POST') {
      const submission = JSON.parse(options.body);
      localAtServerSave = (await pendingMutations()).find(row => row.mutation_id === submission.submission_id);
      detail.questions = [submission];
      data = { question_saved: true, question_submission: submission };
    } else if (path === '/api/library/ask' && options.method === 'POST') {
      if (outcome === 'failed') return new Response(JSON.stringify({
        message: 'Question saved, but paper Q&A failed',
      }), { status: 502 });
      data = { answer: '', abstained: true, mode: 'comprehensive' };
    } else if (path === `/api/library/related/${itemKey}/discover`) data = {
      seed_key: itemKey, admission: { state: 'unknown', basis: null }, context_fingerprint: null,
    };
    else if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') data = { items: [] };
    else if (path === `/api/library/render/${itemKey}`
      || path === `/api/library/deep-review/status?item_key=${itemKey}`) data = { status: 'idle' };
    else if (path === '/api/admin/llm-reachability') data = { stages: [{ stage: 'deep_review', enabled: false }] };
    else { unexpected.push(path); return new Response('{}', { status: 500 }); }
    return new Response(JSON.stringify(data));
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: '/paper/:itemKey', element: <PaperReviewPage /> }], {
    initialEntries: [`/paper/${itemKey}?autoRun=false`],
  });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  try {
    const input = await screen.findByRole('textbox', { name: 'Question about this paper' });
    const notes = screen.getByRole('textbox', { name: 'Manual notes' });
    expect(screen.queryByRole('combobox', { name: 'Answer mode' })).toBeNull();
    expect(writes).toEqual([]);
    fireEvent.change(notes, { target: { value: 'Dirty manual draft' } });
    fireEvent.change(input, { target: { value: question } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    if (outcome === 'failed') await screen.findByText('Ask failed: Question saved, but paper Q&A failed');
    else await screen.findByText(/the model abstained rather than guessing/);
    await waitFor(() => expect(screen.getByRole('list', { name: 'Saved review questions' }).textContent).toContain(question));

    expect(writes.map(([path]) => path)).toEqual(['/api/library/questions', '/api/library/ask']);
    const submission = writes[0][1];
    expect(submission).toEqual({ submission_id: expect.any(String), item_key: itemKey,
      question, origin: 'typed', preset_id: null });
    expect(localAtServerSave).toMatchObject({ mutation_id: submission.submission_id,
      item_key: itemKey, field: 'review_question', operation: 'create', value: submission });
    expect(writes[1][1]).toEqual({ ...submission, mode: 'comprehensive', history: [] });
    expect((await cachedReviewDetail(itemKey)).questions).toEqual(expect.arrayContaining([
      expect.objectContaining(submission),
    ]));
    expect(screen.getByRole('textbox', { name: 'Question about this paper' })).toBe(input);
    expect(notes.value).toBe('Dirty manual draft');
    expect(screen.queryByText(/Zotero note delivery: acknowledged/)).toBeNull();
    expect(unexpected).toEqual([]);
  } finally { client.clear(); router.dispose(); }
});
