// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import Today from './Today.jsx';
import LibraryReadNext from './LibraryReadNext.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each([
  ['today', 'feed', 'add-to-library'], ['today', 'feed', 'trash'],
  ['library', 'quality', 'triage'], ['library', 'quality', 'collection'],
  ['library', 'search', 'triage'], ['library', 'include-read', 'triage'],
])('%s %s filter bounds the %s write to shown selections', async (surface, filter, action) => {
  const writes = [], unexpected = [];
  const papers = [1, 2, 3].map((id) => ({
    item_id: id, item_key: `P${id}`, title: id === 1 ? 'Alpha' : `Beta ${id}`,
    feed_name: id === 1 ? 'arXiv' : 'PubMed', quality_grade: id === 1 ? 'A' : 'D',
    relevance_score: 4, has_pdf: false,
  }));
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    const url = new URL(path, 'http://localhost');
    let data;
    if (options.method === 'POST') {
      writes.push({ path: url.pathname, body: JSON.parse(options.body) });
      data = { added: 1, trashed: 1, job_id: 'J1' };
    } else if (url.pathname === '/api/setup/status') data = { configured: true, zotero: { db_found: true, feed_count: 2 } };
    else if (url.pathname === '/api/daily') data = { papers };
    else if (url.pathname === '/api/daily/triage-status') data = { running: false };
    else if (url.pathname === '/api/feeds/review') data = { items: [] };
    else if (url.pathname === '/api/zotero/collections') data = { items: [{ key: 'C1', name: 'Reading' }] };
    else if (url.pathname === '/api/zotero/tags') data = { items: [] };
    else if (url.pathname === '/api/library/review-fleet/status') data = { status: 'idle' };
    else if (url.pathname === '/api/library/reading-queue') data = {
      items: url.searchParams.get('search') || url.searchParams.get('include_read') === 'true'
        ? papers.slice(0, 1) : papers,
      model_ready: true, status: 'ready', total_unread: 3,
    };
    else { unexpected.push(path); return new Response('{}', { status: 500 }); }
    return new Response(JSON.stringify(data));
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter>
    {surface === 'today' ? <Today /> : <LibraryReadNext />}
  </MemoryRouter></QueryClientProvider>);
  await screen.findByText('Alpha');

  if (surface === 'today') {
    await screen.findByRole('option', { name: /^Reading/ });
    fireEvent.change(screen.getByTitle('Target Zotero collection'), { target: { value: 'C1' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }));
    fireEvent.change(screen.getByTitle('Show only papers from one feed source'), { target: { value: 'arXiv' } });
    const button = screen.getByRole('button', { name: action === 'trash' ? 'Trash 1' : 'Add 1 to library' });
    fireEvent.click(button);
  } else {
    fireEvent.click(screen.getByRole('button', { name: 'Select', exact: true }));
    const rows = screen.getAllByRole('checkbox').filter((c) => c.closest('ol'));
    expect(rows).toHaveLength(3);
    for (const checkbox of rows) {
      fireEvent.click(checkbox);
    }
    if (filter === 'quality') {
      fireEvent.click(screen.getByText('Browse & filter'));
      fireEvent.click(screen.getByRole('button', { name: 'A/B', exact: true }));
    } else if (filter === 'search') {
      const input = screen.getByPlaceholderText(/^Search by meaning/);
      fireEvent.change(input, { target: { value: 'Alpha' } });
      fireEvent.keyUp(input, { key: 'Enter' });
    } else fireEvent.click(screen.getByRole('checkbox', { name: 'Show already-read' }));
    await waitFor(() => expect(screen.queryByText('Beta 2')).toBeNull());
    if (action === 'collection') {
      fireEvent.change(screen.getByTitle('Target Zotero collection for the selected papers'), { target: { value: 'C1' } });
      fireEvent.click(screen.getByRole('button', { name: 'Add to collection (1)' }));
    } else fireEvent.click(screen.getByRole('button', { name: 'Run triage (1)' }));
  }
  await waitFor(() => expect(writes).toHaveLength(1));
  if (surface === 'today') {
    expect(writes[0].path).toBe(`/api/daily/${action}`);
    expect(writes[0].body.item_ids).toEqual([1]);
    if (action === 'add-to-library') expect(writes[0].body.target_collection_key).toBe('C1');
  } else if (action === 'collection') {
    expect(writes[0].path).toBe('/api/zotero/items/P1/collections');
    expect(writes[0].body.add).toEqual([{ collection_key: 'C1' }]);
  } else expect(writes[0].body.item_keys).toEqual(['P1']);
  expect(unexpected).toEqual([]);
  client.clear();
});
