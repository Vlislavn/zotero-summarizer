// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import InlineAnnotate from '../components/library/InlineAnnotate.jsx';
import PaperReviewPage from './PaperReviewPage.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['page', 'compact'])('%s keeps verdict comments until an explicit edit is saved', async (surface) => {
  const writes = [], unexpected = [];
  const detail = {
    item_key: 'P1', title: 'Paper', authors: [], tags: [], collections: [], has_pdf: false,
    verdict: { id: 1, item_key: 'P1', user_priority: 'must_read', comment: 'critical rationale' },
  };
  vi.stubGlobal('localStorage', { getItem: () => null });
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
