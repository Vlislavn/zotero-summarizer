// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PaperReviewPage from './PaperReviewPage.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(['note', 'verdict'])('an acknowledged %s save fences related context before the detail refetch completes', async kind => {
  const detail = { item_key: 'P1', title: 'Paper', tags: [], collections: [], has_pdf: false,
    user_note: 'Old editor note', verdict: { id: 1, item_key: 'P1', user_priority: 'must_read', comment: 'Old editor verdict' } };
  const comments = { availability: 'available', app_state: 'observed', native_state: 'observed',
    records: [{ id: 'old-source', text: 'OLD RELATED SOURCE', source: 'app_note', authorship: 'user' }] };
  let detailReads = 0, releaseDetail;
  const relatedPosts = [], relatedGets = [], unexpected = [];
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/golden/review-detail?item_key=P1') {
      detailReads += 1;
      if (detailReads > 1) return new Promise(resolve => { releaseDetail = () => resolve(new Response(JSON.stringify(detail), { status: 200 })); });
      data = detail;
    } else if (path === '/api/library/related/P1') {
      relatedGets.push(path);
      data = { status: 'ready', context_fingerprint: 'old', boundary: { enabled: false }, items: [], seed_comments: comments };
    } else if (path === '/api/library/related/P1/assess') {
      relatedPosts.push(options.body);
      data = {};
    } else if (path === '/api/golden/review-note' && options.method === 'POST') {
      detail.user_note = JSON.parse(options.body).note;
      data = { saved: true, note_written: false };
    } else if (path === '/api/golden/verdict' && options.method === 'POST') {
      detail.verdict = { ...detail.verdict, ...JSON.parse(options.body) };
      data = detail.verdict;
    } else if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') data = { items: [] };
    else if (path === '/api/library/render/P1' || path === '/api/library/deep-review/status?item_key=P1') data = { status: 'idle' };
    else if (path === '/api/admin/llm-reachability') data = { stages: [{ stage: 'deep_review', enabled: false }] };
    else { unexpected.push(path); return new Response('{}', { status: 500 }); }
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/paper/P1']}>
    <Routes><Route path="/paper/:itemKey" element={<PaperReviewPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Show related saved papers' }));
  await screen.findByText('OLD RELATED SOURCE');

  if (kind === 'note') {
    fireEvent.change(screen.getByPlaceholderText(/^Jot your thoughts/), { target: { value: 'New saved note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
  } else {
    fireEvent.click(screen.getByRole('button', { name: 'Edit', exact: true }));
    fireEvent.change(screen.getByPlaceholderText(/^Why\?/), { target: { value: 'New saved verdict' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update', exact: true }));
  }
  await waitFor(() => expect(detailReads).toBe(2));
  await waitFor(() => expect(screen.queryByText('OLD RELATED SOURCE')).toBeNull());
  expect(screen.getByRole('button', { name: 'Show related saved papers' })).toBeTruthy();
  expect(relatedGets).toHaveLength(1);
  expect(relatedPosts).toEqual([]);

  await act(async () => { releaseDetail(); });

  expect(screen.queryByText('OLD RELATED SOURCE')).toBeNull();
  expect(relatedPosts).toEqual([]);
  expect(unexpected).toEqual([]);
  client.clear();
});
