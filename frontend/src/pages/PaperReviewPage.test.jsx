// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import InlineAnnotate from '../components/library/InlineAnnotate.jsx';
import PaperReviewPage from './PaperReviewPage.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

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

  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/paper/P1']}>
    <Routes><Route path="/paper/:itemKey" element={<PaperReviewPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);

  expect((await screen.findByRole('button', { name: 'Generate review' })).disabled).toBe(true);
  expect(document.querySelector('main').parentElement.className).toContain('lg:grid-cols-[minmax(0,1fr)_20rem]');
  await screen.findByText(/Offline — cached reviews remain readable/);
  expect(screen.queryByText(/Review failed/)).toBeNull();
  client.clear();
});

it('the paper badge counts supported goals, not merely retrieved passages', async () => {
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
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/paper/P1']}>
    <Routes><Route path="/paper/:itemKey" element={<PaperReviewPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);

  expect(await screen.findByText('◎ 2/3 goals')).toBeTruthy();
  expect(screen.getByText('Relevance — 2 of 3 goals addressed')).toBeTruthy();
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
      <MemoryRouter initialEntries={['/paper/P1']}>
        <Routes><Route path="/paper/:itemKey" element={surface === 'page'
          ? <PaperReviewPage /> : <InlineAnnotate itemKey="P1" />} /></Routes>
      </MemoryRouter>
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
