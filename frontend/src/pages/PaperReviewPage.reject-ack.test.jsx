// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import '../testSetup.js';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import PaperReviewPage from './PaperReviewPage.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(['success', 'failure'])('fences a primary dont_read ACK while its secondary tag is pending, then preserves %s', async secondary => {
  const detail = { item_key: 'P1', title: 'Paper', tags: [], collections: [], has_pdf: false,
    verdict: { id: 1, item_key: 'P1', user_priority: 'dont_read', comment: 'Old verdict' } };
  const comments = { availability: 'available', app_state: 'observed', native_state: 'observed',
    records: [{ id: 'old-verdict', text: 'OLD ACK-BOUND RELATED CONTEXT', source: 'human_verdict', authorship: 'user' }] };
  let detailReads = 0, releaseDetail, releaseTag;
  const acknowledgements = [], tagRequests = [], relatedPosts = [], unexpected = [];
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let data;
    if (path === '/api/golden/review-detail?item_key=P1') {
      detailReads += 1;
      if (detailReads > 1) return new Promise(resolve => { releaseDetail = () => resolve(new Response(JSON.stringify(detail), { status: 200 })); });
      data = detail;
    } else if (path === '/api/library/related/P1/discover' && (!options.method || options.method === 'GET')) {
      data = { seed_key: 'P1', seed_title: 'Paper', admission: { state: 'unknown', basis: null }, context_fingerprint: null };
    } else if (path === '/api/library/related/P1') {
      data = { status: 'ready', context_fingerprint: 'old', boundary: { enabled: false }, items: [], seed_comments: comments };
    } else if (path === '/api/library/related/P1/assess') { relatedPosts.push(options.body); data = {}; }
    else if (path === '/api/golden/verdict' && options.method === 'POST') {
      detail.verdict = { ...detail.verdict, ...JSON.parse(options.body) };
      acknowledgements.push(detail.verdict);
      data = detail.verdict;
    } else if (path === '/api/library/reject-tag' && options.method === 'POST') {
      tagRequests.push(JSON.parse(options.body));
      return new Promise(resolve => { releaseTag = () => resolve(new Response(JSON.stringify(
        secondary === 'success' ? { queued: true } : { detail: 'Synthetic secondary tag failure' },
      ), { status: secondary === 'success' ? 200 : 500 })); });
    } else if (path === '/api/zotero/collections' || path === '/api/zotero/tags?limit=300') data = { items: [] };
    else if (path === '/api/library/render/P1' || path === '/api/library/deep-review/status?item_key=P1') data = { status: 'idle' };
    else if (path === '/api/admin/llm-reachability') data = { stages: [{ stage: 'deep_review', enabled: false }] };
    else { unexpected.push(path); return new Response('{}', { status: 500 }); }
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter([
    { path: '/paper/:itemKey', element: <PaperReviewPage /> },
  ], { initialEntries: ['/paper/P1'] })} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Show related saved papers' }));
  await screen.findByText('OLD ACK-BOUND RELATED CONTEXT');
  fireEvent.click(screen.getByRole('button', { name: 'Edit', exact: true }));
  fireEvent.change(screen.getByPlaceholderText(/^Why\?/), { target: { value: 'Acknowledged changed rejection' } });
  fireEvent.click(screen.getByRole('button', { name: 'Update', exact: true }));
  try {
    await waitFor(() => expect(tagRequests).toHaveLength(1));
    expect(acknowledgements).toHaveLength(1);
    expect(acknowledgements[0].comment).toBe('Acknowledged changed rejection');
    expect(screen.queryByText('OLD ACK-BOUND RELATED CONTEXT')).toBeNull();
    expect(screen.getByRole('button', { name: 'Show related saved papers' })).toBeTruthy();
    expect(relatedPosts).toEqual([]);
  } finally {
    await act(async () => { releaseTag?.(); });
    await waitFor(() => expect(detailReads).toBe(2));
    await act(async () => { releaseDetail(); });
  }
  if (secondary === 'failure') await screen.findByText(/Verdict saved, but reject tag was not queued:/);
  else expect(screen.queryByText(/Verdict saved, but reject tag was not queued:/)).toBeNull();
  expect(screen.queryByText('OLD ACK-BOUND RELATED CONTEXT')).toBeNull();
  expect(relatedPosts).toEqual([]);
  expect(unexpected).toEqual([]);
  client.clear();
});
